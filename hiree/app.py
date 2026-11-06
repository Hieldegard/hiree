import html
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = Path(os.environ.get('HIREE_DATA_DIR', str(Path.home() / '.local/share/hiree')))
DEFAULT_TEXT = 'маркетинг руководитель OR head of marketing OR product marketing'

def search_url(text, salary, remote, only_salary):
    text = text.strip()
    if not text or len(text) > 500:
        raise ValueError('Укажите поисковый запрос (не более 500 символов).')
    try:
        salary = int(salary)
    except (ValueError, TypeError):
        raise ValueError('Укажите минимальный доход целым числом.')
    if not 0 <= salary <= 100000000:
        raise ValueError('Минимальный доход должен быть от 0 до 100 000 000 ₽.')
    params = {'text': text, 'area': '113', 'items_on_page': '20'}
    if remote: params['schedule'] = 'remote'
    if salary: params.update(salary=str(salary), currency_code='RUR')
    if only_salary: params['only_with_salary'] = 'true'
    return 'https://hh.ru/search/vacancy?' + urlencode(params)

def db():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(DATA / 'hiree.sqlite3')
    con.execute('CREATE TABLE IF NOT EXISTS vacancies (source TEXT NOT NULL, id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, viewed TEXT NOT NULL, PRIMARY KEY(source,id))')
    con.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
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
        options = {'headless': True}
        if os.environ.get('HIREE_CHROMIUM'):
            options['executable_path'] = os.environ['HIREE_CHROMIUM']
        browser = p.chromium.launch(**options)
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
            settings = dict(con.execute('SELECT key,value FROM settings'))
        esc = html.escape
        cards = ''.join('<article><h2><a rel="noreferrer" href="'+esc(u,quote=True)+'">'+esc(t)+'</a></h2><p>Просмотрено: '+esc(v)+' · Доход и удалённый формат необходимо проверить на странице вакансии</p><details><summary>Описание</summary><pre>'+esc(body)+'</pre></details></article>' for u,t,body,v in rows)
        checked_remote = ' checked' if settings.get('remote', '1') == '1' else ''
        checked_salary = ' checked' if settings.get('only_salary', '0') == '1' else ''
        content = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Hiree — вакансии</title><style>body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;color:#222}input[type=text],input[type=number]{width:100%;box-sizing:border-box;padding:.7rem}label{display:block;margin:.8rem 0}button{padding:.7rem;margin-top:.6rem}article{border-top:1px solid #ddd;padding:1rem 0}pre{white-space:pre-wrap;font:inherit}p{line-height:1.5}</style><h1>Hiree</h1><p>Сбор первой страницы поиска hh.ru. Отклики не отправляются.</p><form method="post" action="/collect"><h2>Настройки поиска</h2><label>Ключевые слова <input type="text" name="text" value="'''+esc(settings.get('text',DEFAULT_TEXT),quote=True)+'''" required maxlength="500"></label><label>Минимальный доход, ₽ в месяц <input type="number" name="salary" min="0" max="100000000" value="'''+esc(settings.get('salary','250000'),quote=True)+'''" required></label><label><input type="checkbox" name="remote" value="1"'''+checked_remote+'''> Удалённая работа</label><label><input type="checkbox" name="only_salary" value="1"'''+checked_salary+'''> Только вакансии с указанной зарплатой</label><p>Без последней отметки включаются вакансии без указанного дохода. Фильтр hh.ru может включать суммы до вычета налогов и диапазоны, лишь частично достигающие порога. Проверяйте доход и формат на странице вакансии.</p><button>Собрать вакансии</button></form><p>'''+str(len(rows))+''' сохранено за все поиски; изменение настроек не удаляет предыдущие результаты. Повторные вакансии не добавляются.</p>'''+cards+'''</html>'''
        self.respond(200,content)
    def do_POST(self):
        if self.path != '/collect': self.send_error(404); return
        if self.headers.get('Origin') not in (None,'http://127.0.0.1:8765','http://localhost:8765'):
            self.send_error(403); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length <= 0 or length > 8192: raise ValueError('Неверный запрос')
            form=parse_qs(self.rfile.read(length).decode())
            text=form.get('text',[''])[0]
            salary=form.get('salary',[''])[0]
            remote=form.get('remote',['0'])[0] == '1'
            only_salary=form.get('only_salary',['0'])[0] == '1'
            url=search_url(text, salary, remote, only_salary)
            with db() as con:
                for key, value in {'text':text, 'salary':salary, 'remote':str(int(remote)), 'only_salary':str(int(only_salary))}.items():
                    con.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, value))
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
