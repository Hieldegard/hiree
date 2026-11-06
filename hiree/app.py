import html
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = Path(os.environ.get('HIREE_DATA_DIR', str(Path.home() / '.local/share/hiree')))
SEARCH = 'https://hh.ru/search/vacancy?'+urlencode({'text':'маркетинг руководитель OR head of marketing OR product marketing','area':'113','schedule':'remote','items_on_page':'20'})

def db():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(DATA / 'hiree.sqlite3')
    con.execute('CREATE TABLE IF NOT EXISTS vacancies (source TEXT NOT NULL, id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, viewed TEXT NOT NULL, PRIMARY KEY(source,id))')
    return con

def vacancy_id(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in ('hh.ru','www.hh.ru') or parts.username or parts.password or parts.port not in (None,443):
        return None
    m = re.fullmatch(r'/vacancy/(\d+)/?', parts.path)
    return m.group(1) if m else None

def save(con, url, title, text):
    ident = vacancy_id(url)
    if not ident or not text.strip():
        raise ValueError('Неверная ссылка или пустое описание вакансии')
    cursor = con.execute('INSERT OR IGNORE INTO vacancies VALUES (?,?,?,?,?,?)', ('hh.ru', ident, 'https://hh.ru/vacancy/'+ident, title.strip(), text.strip(), datetime.now(timezone.utc).isoformat()))
    con.commit()
    return bool(cursor.rowcount)

def collect(search_url):
    from playwright.sync_api import sync_playwright
    parts = urlsplit(search_url)
    if parts.scheme != 'https' or parts.hostname not in ('hh.ru','www.hh.ru') or parts.path != '/search/vacancy' or parts.username or parts.password or parts.port not in (None,443):
        raise ValueError('Нужна ссылка на поиск https://hh.ru/search/vacancy')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get('HIREE_CHROMIUM', '/usr/bin/chromium'))
        try:
            page = browser.new_page()
            response = page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
            if not response or response.status >= 400 or 'captcha' in page.url.lower() or '/search/vacancy' not in urlsplit(page.url).path:
                raise RuntimeError('Поиск недоступен или требуется проверка на hh.ru. Откройте сайт вручную.')
            page.wait_for_timeout(1500)
            links = page.locator('a[href*="/vacancy/"]').evaluate_all('(els) => els.map(e => ({url: e.href, title: e.innerText.trim()}))')
            unique = {}
            for item in links:
                ident = vacancy_id(item['url'])
                if ident and item['title'] and ident not in unique:
                    unique[ident] = item['title']
            if not unique:
                raise RuntimeError('Вакансии не найдены: проверьте поиск или возможное ограничение доступа hh.ru.')
            added = 0
            with db() as con:
                for ident, title in list(unique.items())[:20]:
                    url = 'https://hh.ru/vacancy/' + ident
                    response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    if not response or response.status >= 400 or vacancy_id(page.url) != ident:
                        raise RuntimeError('Чтение вакансии прервано: hh.ru вернул ошибку или запросил проверку. Уже сохранённые вакансии доступны в списке.')
                    body = page.locator('[data-qa="vacancy-description"]')
                    if not body.count():
                        raise RuntimeError('Описание вакансии не найдено. Сбор остановлен; проверьте страницу hh.ru.')
                    added += save(con, url, title, body.first.inner_text())
            return added
        finally:
            browser.close()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/':
            self.send_error(404); return
        with db() as con:
            rows = con.execute('SELECT url,title,text,viewed FROM vacancies ORDER BY viewed DESC').fetchall()
        esc = html.escape
        cards = ''.join('<article><h2><a rel="noreferrer" href="'+esc(u,quote=True)+'">'+esc(t)+'</a></h2><p>Просмотрено: '+esc(v)+' · Доход и удалённый формат необходимо проверить на странице вакансии</p><details><summary>Описание</summary><pre>'+esc(body)+'</pre></details></article>' for u,t,body,v in rows)
        content = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Hiree — вакансии</title><style>body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;color:#222}input{width:100%;box-sizing:border-box;padding:.7rem}button{padding:.7rem;margin-top:.6rem}article{border-top:1px solid #ddd;padding:1rem 0}pre{white-space:pre-wrap;font:inherit}p{line-height:1.5}</style><h1>Hiree</h1><p>Сбор первой страницы поиска hh.ru. Удалённый формат, доход и соответствие необходимо проверить вручную. Отклики не отправляются.</p><form method="post" action="/collect"><label>Ссылка на поиск hh.ru <input name="url" value="'''+esc(SEARCH,quote=True)+'''" required></label><button>Собрать вакансии</button></form><p>'''+str(len(rows))+''' сохранено. Повторные вакансии не добавляются.</p>'''+cards+'''</html>'''
        self.respond(200,content)
    def do_POST(self):
        if self.path != '/collect': self.send_error(404); return
        if self.headers.get('Origin') not in (None,'http://127.0.0.1:8765','http://localhost:8765'):
            self.send_error(403); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length <= 0 or length > 8192: raise ValueError('Неверный запрос')
            url=parse_qs(self.rfile.read(length).decode()).get('url',[''])[0]
            n=collect(url)
            msg=f'Сбор завершён. Новых вакансий: {n}.'
        except Exception as exc:
            msg='Сбор не завершён: '+str(exc)[:500]
        self.respond(200,'<!doctype html><meta charset="utf-8"><p>'+html.escape(msg)+'</p><a href="/">Вернуться к вакансиям</a>')
    def respond(self, status, body):
        data=body.encode(); self.send_response(status); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)

if __name__ == '__main__':
    print('Hiree: http://127.0.0.1:8765', flush=True)
    ThreadingHTTPServer(('127.0.0.1',8765), Handler).serve_forever()
