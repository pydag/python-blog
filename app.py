from urllib.parse import quote
from flask import Flask, render_template, abort, request, redirect, url_for, Response
from email.utils import formatdate
import time
import markdown
from pathlib import Path
import frontmatter
import re
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'dev-key-change-later'  # Нужен для сессий и сообщений (пока не используем, но хорошая привычка)

POSTS_DIR = Path("posts")
POSTS_DIR.mkdir(exist_ok=True)

def slugify(text):
    """Превращает строку в безопасное имя файла: 'Привет Мир!' -> 'privet-mir'"""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)      # Убираем спецсимволы
    text = re.sub(r'[\s_]+', '-', text.strip()) # Пробелы/подчёркивания в дефисы
    return text

def load_posts():
    """Читаем посты, парсим теги и возвращаем список постов + множество всех тегов."""
    posts = []
    all_tags = set()

    for file_path in POSTS_DIR.glob("*.md"):
        with open(file_path, encoding="utf-8") as f:
            post = frontmatter.load(f)

        tags = post.get("tags", [])
        all_tags.update(tags)

        # извлекаем дату из имени файла (формат: 2026-04-26-title.md)
        date_str = file_path.stem[:10]
        try:
            post_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            post_date = datetime.now()

        title = file_path.stem.split("-", 1)[-1].replace("-", " ").title()

        posts.append({
            "filename": file_path.name,
            "url_filename": quote(file_path.name),
            "title": title,
            "preview": post.content[:150] + "...",
            "tags": tags,
            "date": post_date
        })

    return sorted(posts, key=lambda p: p["filename"], reverse=True), sorted(all_tags)

@app.route("/")
def index():
    posts, tags = load_posts()
    return render_template("index.html", posts=posts, tags=tags)

@app.route("/tag/<tag_name>")
def tag_view(tag_name):
    posts, tags = load_posts()
    filtered = [p for p in posts if tag_name in p["tags"]]
    return render_template("index.html", posts=filtered, tags=tags, active_tag=tag_name)

@app.route("/post/<filename>")
def view_post(filename):
    if not filename.endswith(".md") or "/" in filename:
        abort(404)

    file_path = POSTS_DIR / filename
    if not file_path.exists():
        abort(404)

    with open(file_path, encoding="utf-8") as f:
        md_text = f.read()

    html_content = markdown.markdown(md_text, extensions=['nl2br', 'fenced_code', 'tables'])
    title = file_path.stem.split("-", 1)[-1].replace("-", " ").title()
    return render_template("post.html", title=title, content=html_content)

@app.route("/create", methods=["GET", "POST"])
def create_post():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        tags_raw = request.form.get("tags", "").strip()
        content = request.form.get("content", "").strip()

        if not title or not content:
            return "Заполните заголовок и текст поста!", 400

        # Преобразуем "фласк, python" -> ["фласк", "python"]
        tags = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]

        # Формируем имя файла: 2026-04-26-moj-pervyj-post.md
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}-{slugify(title)}.md"
        filepath = POSTS_DIR / filename

        # YAML frontmatter + контент
        tags_yaml = f"tags: [{', '.join(tags)}]" if tags else "tags: []"
        md_content = f"---\n{tags_yaml}\n---\n\n{content}"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)

        return redirect(url_for("view_post", filename=filename))

    return render_template("create.html")

@app.route("/feed")
def feed():
    posts, _ = load_posts()
    site_url = request.url_root.rstrip('/')

    xml = render_template("feed.xml",
                            posts=posts,
                            site_url=site_url,
                            formatdate=formatdate,
                            time=time)
# возвращаем XML с правильным  MIME-типом
    return Response(xml, mimetype="application/rss+xml")

if __name__ == "__main__":
    app.run(debug=True)
