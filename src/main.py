import logging
import re
from collections import Counter
from urllib.parse import urljoin

import requests_cache
from bs4 import BeautifulSoup
from tqdm import tqdm

from configs import configure_argument_parser, configure_logging
from constants import (BASE_DIR, DOWNLOADS_URL, EXPECTED_STATUS, MAIN_DOC_URL,
                       PEP_URL, WHATS_NEW_URL)
from outputs import control_output
from utils import find_tag, get_response


# ---------------------------------------------------------------
# Парсер статей по нововведениям в Python
# ---------------------------------------------------------------


def whats_new(session):
    response = get_response(session, WHATS_NEW_URL)
    soup = BeautifulSoup(response.text, features='lxml')
    main_div = find_tag(soup, 'section', attrs={'id': 'what-s-new-in-python'})
    div_with_ul = find_tag(main_div, 'div', attrs={'class': 'toctree-wrapper'})
    sections_by_python = div_with_ul.find_all('li',
                                              attrs={'class': 'toctree-l1'})
    results = [('Ссылка на статью', 'Заголовок', 'Редактор, Автор')]
    for section in tqdm(sections_by_python, desc='Parsing'):
        version_a_tag = find_tag(section, 'a')
        href = version_a_tag['href']
        version_link = urljoin(WHATS_NEW_URL, href)
        response = get_response(session, version_link)
        soup = BeautifulSoup(response.text, features='lxml')
        h1 = find_tag(soup, 'h1')
        dl = find_tag(soup, 'dl')
        dl_text = dl.text.replace('\n', ' ')
        results.append((version_link, h1.text, dl_text))
    return results


# ---------------------------------------------------------------
# Парсер текущих версий Python с описанием
# ---------------------------------------------------------------


def latest_versions(session):
    response = get_response(session, MAIN_DOC_URL)
    soup = BeautifulSoup(response.text, features='lxml')
    sidebar = find_tag(soup, 'div', attrs={'class': 'sphinxsidebarwrapper'})
    ul_tags = sidebar.find_all('ul')
    for ul in ul_tags:
        if 'All versions' in ul.text:
            a_tags = ul.find_all('a')
            break
    else:
        raise Exception('Ничего не нашлось')
    results = [('Ссылка на документацию', 'Версия', 'Статус')]
    pattern = r'Python (?P<version>\d\.\d+) \((?P<status>.*)\)'
    for a_tag in a_tags:
        link = a_tag['href']
        re_text = re.search(pattern, a_tag.text)
        if re_text:
            version, status = re_text.groups()
        else:
            version, status = a_tag.text, ''
        results.append((link, version, status))
    return results


# ---------------------------------------------------------------
# Парсер, скачивающий документацию
# ---------------------------------------------------------------


def download(session):
    response = get_response(session, DOWNLOADS_URL)
    soup = BeautifulSoup(response.text, features='lxml')
    urls_table = find_tag(soup, 'table', attrs={'class': 'docutils'})
    pdf_a4_tag = find_tag(
        urls_table, 'a', {'href': re.compile(r'.+pdf-a4\.zip$')}
    )
    pdf_a4_link = pdf_a4_tag['href']
    archive_url = urljoin(DOWNLOADS_URL, pdf_a4_link)
    filename = archive_url.split('/')[-1]
    downloads_dir = BASE_DIR / 'downloads'
    downloads_dir.mkdir(exist_ok=True)
    archive_path = downloads_dir / filename
    response = session.get(archive_url, verify=False)
    with open(archive_path, 'wb') as file:
        file.write(response.content)
    logging.info(f'Архив был загружен и сохранён: {archive_path}')


# ---------------------------------------------------------------
# Парсер статусов PEP
# ---------------------------------------------------------------


def pep(session):
    response = get_response(session, PEP_URL)
    soup = BeautifulSoup(response.text, features='lxml')
    all_tables = find_tag(soup, 'section', attrs={'id': 'numerical-index'})
    all_tables = all_tables.find_all('tr')
    total_pep_count = 0
    status_counter = Counter()
    results = [('Статус', 'Количество')]
    for pep_line in tqdm(all_tables, desc='Parsing'):
        total_pep_count += 1
        short_status = pep_line.find('td').text[1:]
        try:
            status_ext = EXPECTED_STATUS[short_status]
        except KeyError:
            status_ext = []
            logging.info(
                f'\nОшибочный статус: {short_status}\n'
                f'Строка PEP: {pep_line}'
            )
        link = find_tag(pep_line, 'a')['href']
        full_link = urljoin(PEP_URL, link)
        response = get_response(session, full_link)
        soup = BeautifulSoup(response.text, features='lxml')
        dl_tag = find_tag(
            soup,
            'dl',
            attrs={'class': 'rfc2822 field-list simple'}
        )
        status_line = dl_tag.find(string='Status')
        if not status_line:
            logging.error(f'{full_link} - не найдена строка статуса')
            continue
        status_line = status_line.find_parent()
        status_int = status_line.next_sibling.next_sibling.string
        if status_int not in status_ext:
            logging.info(
                f'\nНесовпадающие статусы:\n{full_link}\n'
                f'Статус в карточке - {status_int}\n'
                f'Ожидаемые статусы - {status_ext}'
            )
        status_counter[status_int] += 1
    results.extend(status_counter.items())
    sum_from_cards = sum(status_counter.values())
    if total_pep_count != sum_from_cards:
        logging.error(
            f'\n Неправильная сумма:\n'
            f'Всего PEP: {total_pep_count}'
            f'Всего статусов: {sum_from_cards}'
        )
        results.append(('Total', sum_from_cards))
    else:
        results.append(('Total', total_pep_count))
    return results


MODE_TO_FUNCTION = {
    'whats-new': whats_new,
    'latest-versions': latest_versions,
    'download': download,
    'pep': pep
}


def main():
    configure_logging()
    logging.info('Парсер запущен!')
    arg_parser = configure_argument_parser(MODE_TO_FUNCTION.keys())
    args = arg_parser.parse_args()
    logging.info(f'Аргументы командной строки: {args}')
    session = requests_cache.CachedSession()
    if args.clear_cache:
        session.cache.clear()
    parser_mode = args.mode
    results = MODE_TO_FUNCTION[parser_mode](session)
    if results is not None:
        control_output(results, args)
    logging.info('Парсер завершил работу.')


if __name__ == '__main__':
    main()
