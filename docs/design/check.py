#!/usr/bin/env python3
"""Проверка общих компонентов screens.html и kit.html, без внешних зависимостей.

Это структурные проверки, не замена визуальной приёмке в браузере.
Запуск: python3 docs/design/check.py
"""
from html.parser import HTMLParser
from pathlib import Path


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has(self, name):
        return name in self.attrs.get('class', '').split()

    def all(self, tag=None, cls=None):
        result = []
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or child.has(cls)):
                    result.append(child)
                result.extend(child.all(tag, cls))
        return result

    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children).strip()


class Tree(HTMLParser):
    VOID = set('area base br col embed hr img input link meta param source track wbr'.split())

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


HERE = Path(__file__).parent


def load(name):
    source = (HERE / name).read_text(encoding='utf-8')
    tree = Tree(source).root
    ids = [n.attrs['id'] for n in tree.all() if 'id' in n.attrs]
    assert len(ids) == len(set(ids)), f'{name}: повторяются id'
    for css in ('tokens.css', 'components.css'):
        assert (HERE / css).read_text(encoding='utf-8').strip() in source, f'{name}: пересоберите CSS'
    return tree


def one(node, cls):
    found = node.all(cls=cls)
    assert len(found) == 1, f'Ожидался один .{cls}, найдено {len(found)}'
    return found[0]


def check_review(node, editable):
    review = one(node, 'review-criteria')
    rows = review.all(cls='acc__h')
    assert rows and all(r.has('acc__h--score') for r in rows), 'Разбор потерял общую сетку'
    for row in rows:
        # Метки относятся к названию, а не занимают колонку оценки.
        assert not any(isinstance(c, Node) and c.has('pill') for c in row.children)
        one(row, 'acc__t')
    assert 'Принять все' not in node.text(), 'Вернулась лишняя кнопка принятия баллов'
    if editable:
        one(node, 'card__head--review')
    else:
        assert not node.all(cls='scale'), 'Просмотр не должен превращаться в редактор'


screens = load('screens.html')
kit = load('kit.html')
by_id = {n.attrs['id']: n for n in screens.all(cls='screen')}
assert len(by_id) == 22, 'Изменился реестр экранов: проверьте охват'

for ident in ('Р5', 'Р6'):
    page = by_id[ident]
    check_review(page, editable=True)
    one(page, 'review-layout')
    response = one(page, 'card__head--response')
    assert 'Ответ студенту' in response.text() and 'Собрать заново' in response.text()
    assert [b.text() for b in one(page, 'decision-actions').all(cls='btn')] == ['Вернуть на доработку', 'Зачесть']
    assert all(s.attrs.get('class', '').find('scale--ro') < 0 for s in page.all(cls='scale'))

check_review(by_id['К9'], editable=False)
check_review(next(n for n in kit.all('section') if n.attrs.get('id') == 'review'), editable=True)
assert [t.text() for t in by_id['Р6'].all(cls='tab')] == ['Попытка 2', 'Попытка 1']

reference_hint = None
for ident in ('С1', 'С2', 'С3'):
    page = by_id[ident]
    actions = one(page, 'submission-actions')
    secondary = one(actions, 'submission-actions__precheck')
    assert one(secondary, 'btn').text() == 'ИИ-ревью'
    one(secondary, 'caption')
    comment = one(page, 'submission-comment')
    assert comment.tag == 'textarea' and comment.attrs.get('rows') == '2'
    assert 'placeholder' not in comment.attrs
    hint_id = comment.attrs.get('aria-describedby')
    hints = [n for n in page.all(cls='field__hint') if n.attrs.get('id') == hint_id]
    assert len(hints) == 1 and hints[0].text(), f'{ident}: нет постоянной подсказки'
    reference_hint = reference_hint or hints[0].text()
    assert hints[0].text() == reference_hint
    label = 'Отправить исправления' if ident == 'С3' else 'Отправить на ревью'
    assert one(actions, 'btn--pri').text() == label

reviewer_menu = [n.text() for n in one(by_id['Р2'], 'menu').all('a')]
for ident in ('Р3', 'Р4', 'Р5', 'Р6'):
    assert [n.text() for n in one(by_id[ident], 'menu').all('a')] == reviewer_menu

assert len(by_id['К6'].all(cls='crit')) == 1 and len(by_id['К6'].all(cls='acc')) == 8
assert one(by_id['К6'], 'criterion-condition').tag == 'textarea'
assert one(by_id['К6'], 'scale').has('scale--ro'), 'Предпросмотр не должен менять балл'
assert [n.text() for n in one(by_id['К4'], 'date-range').all(cls='field__lbl')] == ['Дата начала', 'Дата окончания']
assert len(by_id['К9'].all(cls='decision-event')) == 4
assert one(by_id['К10'], 'export-context').tag == 'div'
css = (HERE / 'components.css').read_text(encoding='utf-8')
assert 'min-width: var(--review-score-w)' in css and 'height: var(--ctl-s)' in css
assert '.modal.overlay--wide' in css and 'max-width: 720px' in css
print('OK: 22 экрана; семейства ревью и сдачи согласованы; режимы чтения сохранены; CSS актуален.')
