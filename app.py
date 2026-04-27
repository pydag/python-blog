import os
import re
import sqlite3
import time
from datetime import datetime
from functools import wraps
from urllib.parse import quote
from email.utils import formatdate

from flask import Flask, render_template, abort, request, redirect, url_for, Response, session
from markupsafe import Markup, escape
import markdown
from pathlib import Path
import frontmatter

# --- 1. Конфигурация БД ---
DB_NAME = "blog.db"

def get_db():
    """Возвращает соединение с БД. Row позволяет обращаться к столбцам как к словарю."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Создаёт таблицу posts, если её нет."""
    conn = get_db()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    finally:
        conn.close()

# --- 2. Настройка Flask ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

ADMIN_USERNAME = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "python123")  # ⚠️ Смените перед деплоем!

POSTS_DIR = Path("posts")
POSTS_DIR.mkdir(exist_ok=True)

# --- 3. Утилиты и декораторы ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def slugify(text):
    """Превращает строку в безопасный slug: 'Привет Мир!' -> 'privet-mir'"""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text.strip())
    return text

@app.template_filter('highlight')
def highlight_filter(text, query):
    """Подсвечивает совпадения запроса, безопасно экранируя HTML."""
    if not query or not text:
        return text
    safe_text = escape(text)
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    highlighted = pattern.sub(lambda m: f"<mark>{m.group()}</mark>", safe_text)
    return Markup(highlighted)

# --- 4. Инициализация и Миграция ---
init_db()

def migrate_files_to_db():
    """Переносит посты из папки posts/ в SQLite. Удалять файлы НЕ будет."""
    if not POSTS_DIR.exists():
        return

    imported_count = 0
    for file_path in POSTS_DIR.glob("*.md"):
        slug = file_path.stem
        try:
            with open(file_path, encoding="utf-8") as f:
                post = frontmatter.load(f)

            # Пропускаем, если пост уже в БД
            conn = get_db()
            try:
                exists = conn.execute("SELECT id FROM posts WHERE slug=?", (slug,)).fetchone()
                if exists:
                    continue

                tags = ", ".join(post.get("tags", []))  # Исправлено: было "tag"
                title = post.get("title", slug.replace("-", " ").title())

                conn.execute(
                    "INSERT INTO posts (title, slug, content, tags) VALUES (?, ?, ?, ?)",
                    (title, slug, post.content, tags)
                )
                conn.commit()
                imported_count += 1
            finally:
                conn.close()

        except Exception as e:
            print(f"⚠️ Ошибка миграции {file_path.name}: {e}")

    if imported_count > 0:
        print(f"✅ Успешно импортировано {imported_count} постов в БД.")

migrate_files_to_db()

# --- 5. Работа с данными ---
def load_posts():
    """Загружает посты из БД и собирает уникальные теги."""
    conn = get_db()
    try:
        posts = conn.execute("SELECT * FROM posts ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()

    all_tags = set()
    posts_list = []
    for p in posts:
        tag_list = [t.strip() for t in p["tags"].split(",") if t.strip()] if p["tags"] else []
        all_tags.update(tag_list)

        # Парсим дату из SQLite для RSS
        try:
            post_date = datetime.strptime(p["created_at"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            post_date = datetime.now()

        posts_list.append({
            "id": p["id"],
            "title": p["title"],
            "slug": p["slug"],
            "preview": p["content"][:150] + "...",
            "content": p["content"],
            "tags": tag_list,
            "date": post_date
        })
    return posts_list, sorted(all_tags)

# --- 6. Маршруты ---
@app.route("/")
def index():
    posts, tags = load_posts()
    return render_template("index.html", posts=posts, tags=tags)

@app.route("/tag/<tag_name>")
def tag_view(tag_name):
    posts, tags = load_posts()
    filtered = [p for p in posts if tag_name in p["tags"]]
    return render_template("index.html", posts=filtered, tags=tags, active_tag=tag_name)

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("index"))

    posts, tags = load_posts()
    query_lower = query.lower()
    filtered = [p for p in posts if query_lower in p["title"].lower() or query_lower in p["content"].lower()]
    return render_template("index.html", posts=filtered, query=query, tags=tags)

@app.route("/post/<slug>")
def view_post(slug):
    conn = get_db()
    try:
        post = conn.execute("SELECT * FROM posts WHERE slug=?", (slug,)).fetchone()
    finally:
        conn.close()

    if not post:
        abort(404)

    html_content = markdown.markdown(post["content"], extensions=['nl2br', 'fenced_code', 'tables'])
    return render_template("post.html", title=post["title"], content=html_content, slug=post["slug"])

@app.route("/create", methods=["GET", "POST"])
@login_required
def create_post():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        tags_raw = request.form.get("tags", "").strip()
        content = request.form.get("content", "").strip()

        if not title or not content:
            return "Заполните заголовок и текст поста!", 400

        slug = slugify(title)
        tags = ", ".join([t.strip().lower() for t in tags_raw.split(",") if t.strip()])

        conn = get_db()
        try:
            conn.execute("INSERT INTO posts (title, slug, content, tags) VALUES (?, ?, ?, ?)",
                         (title, slug, content, tags))
            conn.commit()
        finally:
            conn.close()

        return redirect(url_for("view_post", slug=slug))
    return render_template("create.html")

@app.route("/post/<slug>/edit", methods=["GET", "POST"])
@login_required
def edit_post(slug):
    conn = get_db()
    try:
        post = conn.execute("SELECT * FROM posts WHERE slug=?", (slug,)).fetchone()
    finally:
        conn.close()

    if not post:
        abort(404)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        tags_raw = request.form.get("tags", "").strip()
        content = request.form.get("content", "").strip()

        if not title or not content:
            return "Заполните заголовок и текст!", 400

        tags = ", ".join([t.strip().lower() for t in tags_raw.split(",") if t.strip()])
        new_slug = slugify(title)

        conn = get_db()
        try:
            conn.execute("UPDATE posts SET title=?, slug=?, content=?, tags=? WHERE slug=?",
                         (title, new_slug, content, tags, slug))
            conn.commit()
        finally:
            conn.close()

        return redirect(url_for("view_post", slug=new_slug))

    return render_template("edit.html", slug=post["slug"], title=post["title"],
                           tags=post["tags"], content=post["content"])

@app.route("/post/<slug>/delete", methods=["POST"])
@login_required
def delete_post(slug):
    conn = get_db()
    try:
        conn.execute("DELETE FROM posts WHERE slug=?", (slug,))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("index"))

@app.route("/feed")
def feed():
    posts, _ = load_posts()
    site_url = request.url_root.rstrip('/')
    xml = render_template("feed.xml",
                          posts=posts,
                          site_url=site_url,
                          formatdate=formatdate,
                          time=time)
    return Response(xml, mimetype="application/rss+xml")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        next_page = request.args.get("next")

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(next_page or url_for("index"))
        return render_template("login.html", error="Неверный логин или пароль")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
