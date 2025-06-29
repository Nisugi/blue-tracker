# viewer.py - Web-based database viewer for BlueTracker

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, g, Response
from werkzeug.serving import run_simple
import json

app = Flask(__name__)
app.config['DB_PATH'] = Path("/data/bluetracker.db")

# For local development, you can override the path
# app.config['DB_PATH'] = Path("./bluetracker.db")

def get_db():
    """Get database connection for current request"""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(
            app.config['DB_PATH'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    """Close database connection at end of request"""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def parse_search_query(query):
    """Parse advanced search syntax"""
    # Extract regex patterns: /pattern/flags
    regex_patterns = []
    remaining_query = query
    
    regex_matches = re.finditer(r'/([^/]+)/([gimsx]*)', query)
    for match in regex_matches:
        pattern = match.group(1)
        flags = match.group(2)
        regex_flags = 0
        if 'i' in flags:
            regex_flags |= re.IGNORECASE
        if 'm' in flags:
            regex_flags |= re.MULTILINE
        if 's' in flags:
            regex_flags |= re.DOTALL
        regex_patterns.append((pattern, regex_flags))
        remaining_query = remaining_query.replace(match.group(0), '')
    
    # Extract quoted phrases: "exact phrase"
    quoted_phrases = []
    quote_matches = re.finditer(r'"([^"]+)"', remaining_query)
    for match in quote_matches:
        quoted_phrases.append(match.group(1))
        remaining_query = remaining_query.replace(match.group(0), '')
    
    # Extract AND terms: word + word
    and_groups = []
    and_matches = re.finditer(r'(\w+)\s*\+\s*(\w+)', remaining_query)
    for match in and_matches:
        and_groups.append([match.group(1), match.group(2)])
        remaining_query = remaining_query.replace(match.group(0), '')
    
    # Remaining words are OR terms
    or_terms = remaining_query.strip().split()
    
    return {
        'regex': regex_patterns,
        'phrases': quoted_phrases,
        'and_groups': and_groups,
        'or_terms': [t for t in or_terms if t]
    }

def matches_search(content, search_params):
    """Check if content matches search parameters"""
    if not content:
        return False
    
    content_lower = content.lower()
    
    # Check regex patterns
    for pattern, flags in search_params['regex']:
        try:
            if re.search(pattern, content, flags):
                return True
        except re.error:
            pass  # Invalid regex, skip
    
    # Check quoted phrases
    for phrase in search_params['phrases']:
        if phrase.lower() in content_lower:
            return True
    
    # Check AND groups (all terms must be present)
    for and_group in search_params['and_groups']:
        if all(term.lower() in content_lower for term in and_group):
            return True
    
    # Check OR terms (any term can match)
    for term in search_params['or_terms']:
        if term.lower() in content_lower:
            return True
    
    # If no search criteria, don't match
    if not (search_params['regex'] or
            search_params['phrases'] or
            search_params['and_groups'] or
            search_params['or_terms']):
        return True   # no criteria at all → match everything
    return False      # criteria exist but none matched

@app.route('/')
def index():
    """Main search interface"""
    from flask import Response
    return Response(search_template, mimetype='text/html')

@app.route('/api/gms')
def get_gms():
    """Get list of all GMs for dropdown"""
    db = get_db()
    cursor = db.execute("""
        SELECT DISTINCT 
            a.author_id,
            COALESCE(g.gm_name, a.author_name, 'Unknown') as display_name
        FROM posts p
        JOIN authors a ON p.author_id = a.author_id
        LEFT JOIN gm_names g ON a.author_id = g.author_id
        ORDER BY display_name
    """)
    
    gms = [{'id': row['author_id'], 'name': row['display_name']} for row in cursor]
    return jsonify(gms)

@app.route('/api/channels')
def get_channels():
    db = get_db()
    rows = db.execute("""
        SELECT c.chan_id,
               c.name,
               COALESCE(p.name, '')  AS parent_name   -- '' for top-level channels
        FROM channels c
        LEFT JOIN channels p ON c.parent_id = p.chan_id
        WHERE c.accessible = 1
        ORDER BY parent_name, c.name
    """)
    grouped = {}
    for r in rows:
        parent = r['parent_name'] or r['name']          # top-level acts as its own group
        grouped.setdefault(parent, []).append({'id': r['chan_id'], 'name': r['name']})

    return jsonify(grouped)   # {"#general":[{id,name},…], "#events":[…]}


@app.route('/api/search')
def search():
    """Search posts with advanced query support"""
    # Get parameters
    query = request.args.get('q', '').strip()
    gm_id = request.args.get('gm_id', '').strip()
    channel_ids = [c for c in request.args.get('channels', '').split(',') if c]
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    
    # Build base query
    sql_parts = ["""
        SELECT 
            p.id,
            p.chan_id,
            p.author_id,
            p.ts,
            p.content,
            p.replayed,
            COALESCE(c.name, p.chan_id)                     AS channel_name,
            COALESCE(g.gm_name, a.author_name, 'Unknown') as author_name
        FROM posts p
        JOIN authors a ON p.author_id = a.author_id
        LEFT JOIN gm_names g ON a.author_id = g.author_id
        LEFT JOIN channels c ON p.chan_id   = c.chan_id
        WHERE 1=1
    """]
    
    params = []
    
    # Add filters
    if gm_id:
        sql_parts.append("AND p.author_id = ?")
        params.append(gm_id)
    
    if channel_ids:
        placeholders = ','.join('?' * len(channel_ids))
        sql_parts.append(f"AND p.chan_id IN ({placeholders})")
        params.extend(channel_ids)
    
    if date_from:
        try:
            dt = datetime.fromisoformat(date_from)
            sql_parts.append("AND p.ts >= ?")
            params.append(int(dt.timestamp() * 1000))
        except:
            pass
    
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            sql_parts.append("AND p.ts <= ?")
            params.append(int(dt.timestamp() * 1000))
        except:
            pass
    
    # Execute query
    db = get_db()
    cursor = db.execute(' '.join(sql_parts) + " ORDER BY p.ts DESC", params)
    
    # Filter by search query if provided
    all_results = []
    search_params = parse_search_query(query) if query else None
    
    for row in cursor:
        if not search_params or matches_search(row['content'], search_params):
            all_results.append({
                'id': row['id'],
                'channel_id': row['chan_id'],
                'channel'   : row['channel_name'],
                'author_id': row['author_id'],
                'author_name': row['author_name'],
                'timestamp': row['ts'],
                'datetime' : datetime.utcfromtimestamp(row['ts']/1000).isoformat() + 'Z',
                'content': row['content'],
                'replayed': row['replayed'],
                'jump_url': f"https://discord.com/channels/{SOURCE_GUILD_ID}/{row['chan_id']}/{row['id']}"
            })
    
    # Paginate results
    total = len(all_results)
    start = (page - 1) * per_page
    end = start + per_page
    
    return jsonify({
        'results': all_results[start:end],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page
    })

@app.route('/api/stats')
def get_stats():
    """Get database statistics"""
    db = get_db()
    
    stats = {}
    
    # Total posts
    cursor = db.execute("SELECT COUNT(*) as count FROM posts")
    stats['total_posts'] = cursor.fetchone()['count']
    
    # Total GMs
    cursor = db.execute("SELECT COUNT(DISTINCT author_id) as count FROM posts")
    stats['total_gms'] = cursor.fetchone()['count']
    
    # Posts by GM
    cursor = db.execute("""
        SELECT 
            COALESCE(g.gm_name, a.author_name, 'Unknown') as name,
            COUNT(*) as count
        FROM posts p
        JOIN authors a ON p.author_id = a.author_id
        LEFT JOIN gm_names g ON a.author_id = g.author_id
        GROUP BY p.author_id
        ORDER BY count DESC
        LIMIT 20
    """)
    stats['top_gms'] = [{'name': row['name'], 'count': row['count']} for row in cursor]
    
    # Recent activity
    cursor = db.execute("""
        SELECT 
            DATE(ts/1000, 'unixepoch') as date,
            COUNT(*) as count
        FROM posts
        WHERE ts > ?
        GROUP BY date
        ORDER BY date DESC
        LIMIT 30
    """, (int((datetime.now().timestamp() - 30*24*60*60) * 1000),))
    stats['recent_activity'] = [{'date': row['date'], 'count': row['count']} for row in cursor]
    
    return jsonify(stats)

# HTML template
search_template = '''
<!DOCTYPE html>
<html>
<head>
    <title>BlueTracker Database Viewer</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/choices.js/public/assets/styles/choices.min.css">
    <script defer src="https://cdn.jsdelivr.net/npm/choices.js/public/assets/scripts/choices.min.js"></script>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 0;
            background: #f0f2f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .header {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .search-box {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .search-box input, .search-box select {
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }
        .search-box input[type="text"] {
            flex: 1;
        }
        .search-help {
            font-size: 12px;
            color: #666;
            margin-top: 5px;
        }
        .filters {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .results {
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .post {
            padding: 15px 20px;
            border-bottom: 1px solid #eee;
        }
        .post:last-child {
            border-bottom: none;
        }
        .post-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 14px;
        }
        .post-author {
            font-weight: 600;
            color: #1a73e8;
        }
        .post-time {
            color: #666;
        }
        .post-content {
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .post-link {
            margin-top: 8px;
            font-size: 12px;
        }
        .post-link a {
            color: #666;
            text-decoration: none;
        }
        .post-link a:hover {
            text-decoration: underline;
        }
        .pagination {
            display: flex;
            justify-content: center;
            gap: 5px;
            padding: 20px;
        }
        .pagination button {
            padding: 5px 10px;
            border: 1px solid #ddd;
            background: white;
            cursor: pointer;
            border-radius: 4px;
        }
        .pagination button:hover {
            background: #f0f2f5;
        }
        .pagination button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        .pagination .current {
            background: #1a73e8;
            color: white;
            border-color: #1a73e8;
        }
        .stats {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }
        .stat-box {
            text-align: center;
        }
        .stat-number {
            font-size: 32px;
            font-weight: 600;
            color: #1a73e8;
        }
        .stat-label {
            color: #666;
            font-size: 14px;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #666;
        }
        .error {
            background: #fee;
            color: #c00;
            padding: 10px;
            border-radius: 4px;
            margin: 10px 0;
        }
        .highlight {
            background: #ff0;
            padding: 2px;
        }
        .filters { 
            align-items: center; 
            }
        /* size of the closed input */ 
        .choices__inner { 
            padding: 6px 40px 6px 12px;
            min-height: 38px;
            line-height: 22px; 
        }
        .choices__list--multiple .choices__item {
            margin: 0 4px 4px 0; 
        } 
        /* keep the first row compact */
        .filters { display:flex; gap:10px; flex-wrap:wrap; }

        /* second row spans full card width */
        .channel-row { margin-top:12px; }
        .channel-row { width:100%; }
        
        /* let Choices fill the row */
        #channelSelect + .choices { width:100%; max-width:none; }

        /* when closed, keep height 40 px like other inputs */
        .choices__inner      { min-height:40px; }

        .post-author, .post-channel { cursor: pointer; }
        .post-author:hover,
        .post-channel:hover         { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>BlueTracker Database Viewer</h1>
            
            <div class="search-box">
                <input type="text" id="searchQuery" placeholder="Search posts..." value="">
                <select id="gmFilter">
                    <option value="">All GMs</option>
                </select>
                <button onclick="search()">Search</button>
            </div>
            
            <div class="search-help">
                <strong>Search syntax:</strong> 
                word (any word) | 
                "exact phrase" | 
                word + word (both required) | 
                /regex/i (regex with flags) |
                Combine multiple patterns
            </div>
            
            <!-- 1st row = GM + dates + clear -->
            <div class="filters">
                <input type="date" id="dateFrom">
                <input type="date" id="dateTo">
                <button onclick="clearFilters()">Clear Filters</button>
            </div>

            <!-- 2nd row = full-width channel picker -->
            <div class="channel-row">
                <select id="channelSelect" multiple></select>
            </div>

        </div>
        
        <div class="stats" id="stats" style="display:none;">
            <h2>Database Statistics</h2>
            <div class="stats-grid" id="statsGrid"></div>
        </div>
        
        <div class="results" id="results">
            <div class="loading">Loading...</div>
        </div>
        
        <div class="pagination" id="pagination"></div>
    </div>
    
    <script>
        // Source guild ID from config
        const SOURCE_GUILD_ID = '226045346399256576';
        
        let currentPage = 1;
        let totalPages = 1;
        
        // Load GMs for dropdown
        async function loadGMs() {
                try {
                const response = await fetch('/api/gms');
                const gms = await response.json();
                
                const select = document.getElementById('gmFilter');
                gms.forEach(gm => {
                    const option = document.createElement('option');
                    option.value = gm.id;
                    option.textContent = gm.name;
                    select.appendChild(option);
                });
            } catch (error) {
                console.error('Failed to load GMs:', error);
            }
        }

        // Load channels selection dropdown
        let channelChoices;   // Choices instance

        async function loadChannels() {
            const res  = await fetch('/api/channels');
            const data = await res.json();          // {group:[items]}

            const select = document.getElementById('channelSelect');
            for (const [group, items] of Object.entries(data)) {
                const optgroup = document.createElement('optgroup');
                optgroup.label = group;
                items.forEach(ch => {
                    const opt   = document.createElement('option');
                    opt.value   = ch.id;
                    opt.text    = ch.name;
                    optgroup.appendChild(opt);
                });
                select.appendChild(optgroup);
            }

            channelChoices = new Choices(select, {
                removeItemButton:true,
                placeholderValue:'Select channels…',
                searchPlaceholderValue:'Type to search',
                shouldSort:false
            });
        }

        function selectedChannelIds() {
            return channelChoices ? channelChoices.getValue(true).join(',') : '';
        }

        function readFiltersFromUI() {
            return {
                q:        document.getElementById('searchQuery').value,
                gm:       document.getElementById('gmFilter').value,
                channels: selectedChannelIds(),        // already returns "id1,id2"
                from:     document.getElementById('dateFrom').value,
                to:       document.getElementById('dateTo').value,
                page:     currentPage
           };
        }

        function applyFiltersToUI(f) {
            document.getElementById('searchQuery').value = f.q   || '';
            document.getElementById('gmFilter').value     = f.gm || '';
            document.getElementById('dateFrom').value     = f.from || '';
            document.getElementById('dateTo').value       = f.to   || '';
        
            /* channels: set via Choices.js */
            if (channelChoices) {
                channelChoices.removeActiveItems();
                (f.channels || '').split(',').filter(Boolean).forEach(id => {
                    channelChoices.setChoiceByValue(id);
                });
            }
            currentPage = parseInt(f.page || 1, 10);
        }

        // Load statistics
        async function loadStats() {
            try {
                const response = await fetch('/api/stats');
                const stats = await response.json();
                
                const statsGrid = document.getElementById('statsGrid');
                statsGrid.innerHTML = `
                    <div class="stat-box">
                        <div class="stat-number">${stats.total_posts.toLocaleString()}</div>
                        <div class="stat-label">Total Posts</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-number">${stats.total_gms}</div>
                        <div class="stat-label">Total GMs</div>
                    </div>
                `;
                
                document.getElementById('stats').style.display = 'block';
            } catch (error) {
                console.error('Failed to load stats:', error);
            }
        }
        
        // Search function
        async function search(page = 1) {
            currentPage = page;

            const f = readFiltersFromUI();
            f.page = page;
            const url = new URL(window.location);
            Object.entries(f).forEach(([k, v]) =>
                v ? url.searchParams.set(k, v) : url.searchParams.delete(k)
            );
            history.replaceState(null, '', url);
            localStorage.setItem('btFilters', JSON.stringify(f));
            
            const params = new URLSearchParams({
                q: f.q, gm_id: f.gm, channels: f.channels,
                date_from: f.from, date_to: f.to,
                page: f.page, per_page: 50
            });
            
            const resultsDiv = document.getElementById('results');
            resultsDiv.innerHTML = '<div class="loading">Searching...</div>';
            
            try {
                const response = await fetch('/api/search?' + params);
                const data = await response.json();
                
                totalPages = data.total_pages;
                
                if (data.results.length === 0) {
                    resultsDiv.innerHTML = '<div class="loading">No results found</div>';
                    return;
                }
                
                resultsDiv.innerHTML = data.results.map(post => `
                    <div class="post">
                        <div class="post-header">
                            <span class="post-author"
                                data-author-id="${post.author_id}">
                                ${escapeHtml(post.author_name)}
                            </span>
                            <span> · </span>
                            <span class="post-channel"
                                data-chan-id="${post.channel_id}">
                                #${escapeHtml(post.channel)}
                            </span>
                            <span class="post-time">${formatDate(post.datetime)}</span>
                        </div>
                        <div class="post-content">${highlightSearch(escapeHtml(post.content || '(no content)'))}</div>
                        <div class="post-link">
                            <a href="${post.jump_url}" target="_blank">Jump to message ↗</a>
                            ${post.replayed ? ' • ✓ Replayed' : ' • ⏳ Not replayed'}
                        </div>
                    </div>
                `).join('');
                
                updatePagination();
                
            } catch (error) {
                resultsDiv.innerHTML = '<div class="error">Search failed: ' + error.message + '</div>';
            }
        }
        
        // Update pagination buttons
        function updatePagination() {
            const paginationDiv = document.getElementById('pagination');
            
            if (totalPages <= 1) {
                paginationDiv.innerHTML = '';
                return;
            }
            
            let buttons = [];
            
            // Previous button
            buttons.push(`<button onclick="search(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>Previous</button>`);
            
            // Page numbers
            for (let i = 1; i <= Math.min(totalPages, 10); i++) {
                if (i === currentPage) {
                    buttons.push(`<button class="current">${i}</button>`);
                } else {
                    buttons.push(`<button onclick="search(${i})">${i}</button>`);
                }
            }
            
            if (totalPages > 10) {
                buttons.push('<span>...</span>');
                buttons.push(`<button onclick="search(${totalPages})">${totalPages}</button>`);
            }
            
            // Next button
            buttons.push(`<button onclick="search(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>Next</button>`);
            
            paginationDiv.innerHTML = buttons.join('');
        }
        
        // Clear all filters
        function clearFilters() { 
            document.getElementById('searchQuery').value = '';
            document.getElementById('gmFilter').value = '';
            channelChoices.removeActiveItems(); 
            document.getElementById('dateFrom').value = '';
            document.getElementById('dateTo').value = '';
            history.replaceState(null,'',location.pathname);
            localStorage.removeItem('btFilters');
            search(); 
        }
        
        // Utility functions
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function formatDate(iso) {
            return new Date(iso).toLocaleString();
        }
        
        function highlightSearch(text) {
            const query = document.getElementById('searchQuery').value;
            if (!query) return text;
            
            // Simple highlight for quoted phrases
            const phrases = query.match(/"([^"]+)"/g);
            if (phrases) {
                phrases.forEach(phrase => {
                    const clean = phrase.replace(/"/g, '');
                    try {
                        const regex = new RegExp(escapeRegex(clean), 'gi');
                        text = text.replace(regex, match => `<span class="highlight">${match}</span>`);
                    } catch (_) { /* ignore malformed pattern */ }
                });
            }
            return text;
        }
        
        function escapeRegex(string) {
            return string.replace(/[.*+?^${}()|\\[\\]\\\\]/g, '\\\\$&');
        }
        
        // Initialize
        window.onload = async function() {
            await loadGMs();
            await loadChannels();
            await loadStats();
            let f;
            if (location.search.length > 1) {
                f = Object.fromEntries(new URLSearchParams(location.search));
            } else {
                f = JSON.parse(localStorage.getItem('btFilters') || '{}');
            }
            applyFiltersToUI(f);

            /* 2. run the first search */
            search(parseInt(f.page || 1, 10));
            
            // Enter key in search box
            document.getElementById('searchQuery').addEventListener('keypress', function(e) {
                if (e.key === 'Enter') search();
            });
            document.getElementById('results').addEventListener('click', e => {
                // GM name clicked?
                const authorSpan = e.target.closest('[data-author-id]');
                if (authorSpan) {
                    document.getElementById('gmFilter').value = authorSpan.dataset.authorId;
                    search(1);     // reset to page 1
                    return;
                }
                // Channel name clicked?
                const chanSpan = e.target.closest('[data-chan-id]');
                if (chanSpan && channelChoices) {
                    const id = chanSpan.dataset.chanId;
                    /* add only if not already selected */
                    if (!channelChoices.getValue(true).includes(id)) {
                        channelChoices.setChoiceByValue(id);
                    }
                    search(1);
                }
            });
        };
    </script>
</body>
</html>
'''

# Import SOURCE_GUILD_ID from config
try:
    from bot.config import SOURCE_GUILD_ID
except ImportError:
    SOURCE_GUILD_ID = '226045346399256576'  # Fallback

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='BlueTracker Database Viewer')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', default=5000, type=int, help='Port to bind to')
    parser.add_argument('--db', help='Path to database file')
    args = parser.parse_args()
    
    if args.db:
        app.config['DB_PATH'] = Path(args.db)
    
    print(f"Starting viewer on http://{args.host}:{args.port}")
    print(f"Database: {app.config['DB_PATH']}")
    run_simple(args.host, args.port, app, use_reloader=True, use_debugger=True)
