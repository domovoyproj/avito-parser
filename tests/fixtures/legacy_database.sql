BEGIN TRANSACTION;
CREATE TABLE ai_settings (
                    id INTEGER PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT 'deepseek',
                    api_key TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT 'deepseek-chat',
                    api_base TEXT NOT NULL DEFAULT 'https://api.deepseek.com',
                    prompt_template TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE custom_scoring_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT NOT NULL,
                    label TEXT NOT NULL,
                    score_delta INTEGER NOT NULL DEFAULT -25,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE items (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    price INTEGER,
                    old_price INTEGER,
                    price_string TEXT,
                    url TEXT NOT NULL,
                    address TEXT,
                    metro TEXT,
                    published_at TEXT,
                    description TEXT,
                    main_image TEXT,
                    images_json TEXT,
                    params_json TEXT,
                    seller_json TEXT,
                    delivery_available BOOLEAN DEFAULT 0,
                    is_vip BOOLEAN DEFAULT 0,
                    category TEXT,
                    search_query_id INTEGER,
                    is_favorite BOOLEAN DEFAULT 0,
                    is_hidden BOOLEAN DEFAULT 0,
                    is_reserved BOOLEAN DEFAULT 0,
                    is_closed BOOLEAN DEFAULT 0,
                    market_avg_price INTEGER,
                    is_hot_deal BOOLEAN DEFAULT 0,
                    deal_score INTEGER DEFAULT 50,
                    deal_grade TEXT DEFAULT 'FAIR',
                    deal_reasons_json TEXT,
                    ai_summary TEXT,
                    detected_flaws_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                , content_hash TEXT, closed_at TIMESTAMP);
INSERT INTO "items" VALUES('legacy','Legacy item',100,NULL,NULL,'https://www.avito.ru/legacy',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,0,0,NULL,1,0,0,0,0,NULL,0,50,'FAIR',NULL,NULL,NULL,'2026-01-01T00:00:00','2026-01-01T00:00:00',NULL,NULL);
CREATE TABLE price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    old_price INTEGER NOT NULL,
                    new_price INTEGER NOT NULL,
                    delta INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
                );
CREATE TABLE searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    min_price INTEGER,
                    max_price INTEGER,
                    check_interval_min INTEGER DEFAULT 10,
                    enabled BOOLEAN DEFAULT 1,
                    last_checked_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                , active_hours_start INTEGER DEFAULT 0, active_hours_end INTEGER DEFAULT 24);
INSERT INTO "searches" VALUES(1,'Legacy search','https://www.avito.ru/legacy',NULL,NULL,10,1,NULL,'2026-10-03 00:54:40',0,24);
CREATE TABLE seller_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_name TEXT UNIQUE NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE sent_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    search_query_id INTEGER,
                    notification_type TEXT NOT NULL,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(item_id, search_query_id, notification_type)
                );
CREATE TABLE telegram_chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    notify_new BOOLEAN NOT NULL DEFAULT 1,
                    notify_drops BOOLEAN NOT NULL DEFAULT 1,
                    send_photos BOOLEAN NOT NULL DEFAULT 1,
                    delivery_only BOOLEAN NOT NULL DEFAULT 0,
                    min_discount_pct INTEGER NOT NULL DEFAULT 0,
                    min_deal_score INTEGER NOT NULL DEFAULT 0,
                    keyword_filter TEXT,
                    blacklisted_sellers_json TEXT,
                    quiet_hours_enabled BOOLEAN NOT NULL DEFAULT 0,
                    quiet_hours_start INTEGER NOT NULL DEFAULT 23,
                    quiet_hours_end INTEGER NOT NULL DEFAULT 7,
                    only_below_market BOOLEAN NOT NULL DEFAULT 0,
                    below_market_pct INTEGER NOT NULL DEFAULT 15,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE user_sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'operator',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login_at TIMESTAMP
                );
CREATE TABLE webhook_settings (
                    id INTEGER PRIMARY KEY,
                    url TEXT NOT NULL DEFAULT '',
                    enabled BOOLEAN NOT NULL DEFAULT 0,
                    send_new BOOLEAN NOT NULL DEFAULT 1,
                    send_drops BOOLEAN NOT NULL DEFAULT 1,
                    min_deal_score INTEGER NOT NULL DEFAULT 0,
                    secret TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE INDEX idx_items_deal_score ON items(deal_score);
CREATE INDEX idx_items_deal_grade ON items(deal_grade);
CREATE INDEX idx_items_search_id ON items(search_query_id);
CREATE INDEX idx_items_price ON items(price);
CREATE INDEX idx_items_created_at ON items(created_at);
CREATE INDEX idx_items_favorite ON items(is_favorite);
CREATE INDEX idx_notif_lookup ON sent_notifications(item_id, search_query_id, notification_type);
CREATE INDEX idx_price_history_item ON price_history(item_id);
CREATE INDEX idx_sessions_token ON user_sessions(token);
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_audit_created ON audit_logs(created_at);
CREATE INDEX idx_items_content_hash ON items(content_hash);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',1);
INSERT INTO "sqlite_sequence" VALUES('searches',1);
COMMIT;
