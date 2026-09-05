const auditCases = [
  {id:'navigation',screen:'Р2',name:'Навигация ревьюера',title:'Меню не должно меняться при переходе между экранами',kind:'Последовательность навигации',
   summary:'Оставляем оба списка на странице: свои работы и обзор пула. Правка касается реального расхождения Р2 с Р3–Р6, а не гипотезы о новой информационной архитектуре.',
   changes:[
    ['На Р2 в меню «Статистика» и «Настройки», на Р3–Р6 вместо них «Кабинет» с двумя вкладками.','То же меню «Мои работы / Пул / Кабинет» на всех экранах ревьюера. Верхняя кнопка «Настройки» как короткий путь остаётся.','reviewer-nav'],
    ['Таблица называется «Активные», но в ней есть зачтённая работа 4776.','Название «Мои работы» согласовано с меню и не обещает фильтр, которого в данных нет. Строки сохраняются.','reviewer-list']
   ],caution:'В Р2 работа 4790 взята 17 февраля, а в Р5 сдана 18 февраля. Эти даты нужно получить из общей фикстуры, не править визуально независимо.'},
  {id:'review',screen:'Р5',name:'Карточка ревью',title:'Выровнять разбор, дать ответу и кнопкам достаточно места',kind:'Вёрстка и ясность действий',
   summary:'Сохраняем левую колонку с работой, ИИ, ответом и итогом, правую с требованиями, все цитаты, баллы и порядок рубрики. Меняем только внутреннюю геометрию.',
   changes:[
    ['Шкалы имеют разную ширину; длинное требование и метка «оценочное» конкурируют за одну строку.','Общий правый столбец шкал шириной 136 px; метка относится к названию. Зона выбора 32 px вместо 22 px. Длинный код прокручивается внутри цитаты.','review-grid'],
    ['«Ответ студенту» и «Собрать заново» стоят в одной тесной шапке; два длинных решения делят ширину 384 px.','У названия и действия отдельные строки. Кнопки решения располагаются одна под другой: текст читается целиком.','review-response'],
    ['«Принять все» не объясняет, что произойдёт: баллы уже заполнены и по описанию сохраняются при финальном решении.','Вместо повторного подтверждения — короткая подпись о редактировании баллов. Финальные кнопки и ручное сохранение остаются.','review-grid'],
    ['Первое выполненное требование раскрыто ради «Нарушений не найдено».','Свернуть только этот пустой блок. Остальные требования не сортировать, не прятать и не сокращать.','review-grid']
   ],caution:'Отдельное продуктовое противоречие: итог 3,5 при пороге 4, но доступно «Зачесть». К5 говорит, что штраф нельзя обойти. В предложении кнопка оставлена как в исходнике: нужна единая политика исключений. «Своё требование» тоже требует определения влияния на максимум 6 баллов.'},
  {id:'submit',screen:'С1',name:'Форма сдачи',title:'Связать пояснение с нужным действием',kind:'Композиция формы',
   summary:'Витринная плашка, условие задания и полная рубрика сохраняются. Главное действие остаётся первым слева, как предусмотрено вашим дизайн-паком.',
   changes:[
    ['Две кнопки и длинная подпись — три соседних элемента flex-wrap. При переносе подпись может оторваться от ИИ-ревью.','Объединить ИИ-кнопку и пояснение в одну группу: подпись прямо под кнопкой. При сужении переносится вся группа.','submit-actions'],
    ['Комментарий — однострочное поле с длинным примером, который невозможно прочесть целиком.','Многострочное поле на две строки. Пример вынесен в постоянную подсказку, видимую и после ввода.','submit-comment']
   ]},
  {id:'ai',screen:'С2',name:'После ИИ-ревью',title:'Совет модели не должен выглядеть запретом на отправку',kind:'Микротекст и группировка',
   summary:'У студента по-прежнему нет баллов от модели. Результат видит ревьюер, как прямо указано в исходнике. История проверок сохраняет свой порядок.',
   changes:[
    ['«К отправке не готово» звучит как запрет, хотя зелёная кнопка отправки доступна. Ниже длинное пояснение опровергает категоричный заголовок.','«Есть замечания перед отправкой»; краткое пояснение: «Можно отправить работу сейчас или сначала внести правки. Решение принимает ревьюер».','ai-advice'],
    ['Предлагается «прикрепить новую ссылку», хотя студент может обновить тот же репозиторий.','«Доработайте работу и проверьте ссылку в форме ниже». Не требовать ненужного действия.','ai-advice'],
    ['Подпись о последней проверке отрывается от ИИ-кнопки.','Та же группа действий, что на С1: компонент сохраняется между состояниями.','submit-actions']
   ],caution:'В истории две завершённые проверки, но осталось 2 из 3. Если каждый запуск расходует попытку, счётчик неверен. Без правила списания число не исправлял.'},
  {id:'resubmit',screen:'С3',name:'Нужны правки',title:'Одно состояние должно называться одинаково на всём экране',kind:'Последовательность',
   summary:'Срок пересдачи уже указан в плашке — не дублируем его новой карточкой. Замечания, оценка и текст ревьюера сохраняются.',
   changes:[
    ['В плашке оранжевое «Нужны правки», а в результате красное «не прошла проверку». Второе можно принять за окончательный отказ.','В результате то же «Нужны правки» и тот же смысл цвета. Красными остаются невыполненные критерии.','resubmit-verdict'],
    ['Форма и действие называются как при первой сдаче: «Ваша работа», «Отправить на ревью».','«Исправленная работа» и «Отправить исправления». Ссылка и пояснение о сохранении первой попытки остаются.','submit-actions']
   ]},
  {id:'criteria',screen:'К6',name:'Редактор критериев',title:'Сохранить новый список, улучшить редактирование и выравнивание',kind:'Читаемость и доступность',
   summary:'В текущем К6 уже девять критериев, аккордеон и расчёт шкалы из баллов и шага. Всё это сохраняем. Правки касаются длинного условия и внутренней геометрии списка.',
   changes:[
    ['У «Выполнено, если» уже есть подпись, но однострочный input скрывает конец формулировки.','Полноширинное многострочное поле под той же подписью. Текст условия не сокращён.','criterion-editor'],
    ['В свёрнутых строках класс проверки имеет разную длину; свободный flex сдвигает границы колонок.','Общие столбцы названия, класса проверки, балла и стрелки. Длинные названия и классы переносятся внутри своих колонок.','criterion-list'],
    ['У перестановки есть drag-handle, но нет показанного управления с клавиатуры.','У раскрытого критерия — две небольшие кнопки «Вверх» / «Вниз» и текущий номер. Остальные строки остаются компактными.','criterion-order']
   ],caution:'Это предложение пересобрано после обновления К6. Старое замечание о трёх показанных критериях больше не актуально.'},
  {id:'registry',screen:'К8',name:'Реестр домашек',title:'Сделать фильтры одной строкой управления таблицей',kind:'Плотность и поиск',
   summary:'Семь статусов, восемь счётчиков, данные таблицы и действие выгрузки сохраняются. Меняется способ выбора статуса, а не жизненный цикл работы.',
   changes:[
    ['Восемь длинных вкладок плюс фильтр задания переносятся на несколько строк. Подчёркивание активной вкладки теряет опору.','Поиск, задание и статус собраны над одной таблицей. Статусы доступны в раскрывающемся списке со всеми исходными счётчиками.','registry-filters'],
    ['Сверху «Показаны 8 из 88», но нет продолжения списка.','В прототипе подпись «В макете показаны 8 из 88». Для реализации отдельно нужен выбор: пагинация или «Показать ещё». Не рисуем работающую пагинацию без строк.','registry-filters']
   ],caution:'Раскрывающийся список скрывает обзор всех статусов. Это компромисс ради компактности. Если координатор постоянно сравнивает численность статусов, сохранить одну горизонтально прокручиваемую полосу вкладок — допустимая альтернатива.'},
  {id:'cohort',screen:'К4',name:'Создание потока',title:'Различить начало, окончание и служебный ID',kind:'Понятность полей',
   summary:'Сохраняем модальное окно, число сущностей и автоматическое формирование ID. Тексты про Stepik и зачисление остаются.',
   changes:[
    ['Два поля дат имеют одну подпись «Даты». По отдельности непонятно, что означает каждое.','У каждого поля своя подпись: «Дата начала» и «Дата окончания». На узком экране они располагаются друг под другом.','cohort-dates'],
    ['ID показан как выключенное поле: светлый текст и недоступное редактирование выглядят как ограничение прав.','ID как обычное выделяемое значение с прежней подсказкой о формировании. Это информация, а не неработающий input.','cohort-id']
   ]},
  {id:'history',screen:'К9',name:'История решений',title:'Дать событиям журнала ширину для чтения',kind:'Иерархия текста',
   summary:'Карточка работы слева и разбор справа остаются на своих местах. В журнале те же четыре события, те же времена и баллы.',
   changes:[
    ['Длинное событие и дата делят строку .kv шириной около 340 px. Они сжимают друг друга и рвутся на короткие строки.','Событие занимает всю строку; дата — спокойная подпись под ним. Отступ и тонкая линия разделяют события.','history-log'],
    ['«Модель → человек → итог» есть в данных, но почти не считывается в плотной сетке ключ — значение.','Последнее событие остаётся последним, без повторной метрики и без декоративной ленты: последовательность читается по блокам.','history-log']
   ]},
  {id:'export',screen:'К10',name:'Окно выгрузки',title:'У широкого окна должна быть действительно широкая вёрстка',kind:'Конкретный конфликт CSS',
   summary:'Состав колонок, формат, правила обработки незакрытых работ и 47 строк остаются как в исходнике. Это уже предусмотренные возможности, а не новые функции.',
   changes:[
    ['.overlay--wide задаёт width:720px, но .modal одновременно ограничивает max-width:560px. Широкое окно не расширяется.','Согласованный модификатор .modal.overlay--wide: ширина и максимум 720 px, с полями до границы небольшого окна.','export-width'],
    ['Область «Что выгружаем» выглядит редактируемым input, хотя содержит задание и поток из предыдущего экрана.','Тот же контекст показан читаемым текстом на всю ширину. Название полностью переносится на строки.','export-context']
   ]}
];

const inventory = [
 ['С1','Правки показаны','Группировка действий и многострочный комментарий.'],
 ['С2','Правки показаны','Мягче вывод ИИ; уточнить списание попыток: две проверки и остаток 2 из 3.'],
 ['С3','Правки показаны','Единое название состояния и действие пересдачи. Срок уже есть.'],
 ['С4','Сохранить основу','Компактная таблица решает задачу. Не добавлять сводки и разделение на табы без объёма данных.'],
 ['С5','Локальная проверка','Архив и история уместны. Объяснить, куда ведёт «Открыть страницу сдачи» у закрытой работы: просмотр или новая попытка.'],
 ['Р1','Уточнить содержание','Описание обещает аккаунт без одобрений; экран говорит «доступ у сотрудников» и «если вас нет в списке». Согласовать реальный доступ, затем править текст.'],
 ['Р2','Правки показаны','Меню отличается от Р3–Р6; в «Активных» есть зачтённая работа. Исправить названия, сохранив композицию.'],
 ['Р3','Сохранить основу','Две настройки понятны. Для формы нужны состояния несохранённых изменений и ошибки, а не обещание нового автосохранения.'],
 ['Р4','Уточнить измерение','Не убирать согласие с моделью: экран уже объясняет, что расхождение не ошибка. Для 6% расхождения определить формулу и размер сравниваемой выборки.'],
 ['Р5','Правки показаны','Вёрстка шкал, ответа и решений; отдельно правила зачёта и добавленного критерия.'],
 ['Р6','Распространить компонент','Тот же компонент Р5; вкладки попыток сохраняются. Не добавлять дублирующий пересказ прошлой попытки без проверки потребности.'],
 ['К1','Проверить данные','В плитке 3 работы дольше 3 дней, а колонка «Зависло» содержит 9 и 3 — похоже, это весь пул. Назвать «Без ревьюера» или изменить выборку.'],
 ['К1а','Сохранить основу','Поиск уже показан отдельным состоянием. Для реализации: клавиатура, отсутствие совпадений и понятное различение работ одного студента.'],
 ['К2','Сохранить основу','Четыре курса не требуют новых фильтров. Переход к редактированию должен иметь фокус и hover.'],
 ['К3','Локальная правка','«Кто ведёт курс» как справочное значение, если это действительно не редактируется; не как disabled input.'],
 ['К4','Правки показаны','Подписи дат и информационное значение ID.'],
 ['К5','Разрешить противоречие','Конкретный дедлайн в переиспользуемом задании и подпись о настройке на шаге 3. Убрать поле здесь либо назвать «срок по умолчанию», если он реально наследуется.'],
 ['К6','Правки показаны','Полноширинные формулировки и управление порядком.'],
 ['К7','Проверить семантику','Рядом с полосами типичных ошибок указать выборку: все закрытые работы или весь поток. Не вводить автоназначение вопреки pull-процессу.'],
 ['К8','Правки показаны','Компактные фильтры. Способ продолжения списка отдельно от восьми демонстрационных строк.'],
 ['К9','Правки показаны','Читаемый журнал. Текст ответа уже доступен ссылкой внизу — отсутствие ответа не является дефектом.'],
 ['К10','Правки показаны','Счётчик 47 строк и состав колонок уже есть. Исправить конфликт width и max-width у широкого окна.']
];

function applyProposal(doc, item) {
 const root=doc.body;
 const query=(s)=>root.querySelector(s);
 const all=(s)=>[...root.querySelectorAll(s)];
 const card=(name)=>all('.card').find(el=>el.querySelector('.card__head h4')?.textContent.trim()===name);
 const mark=(el,id)=>{if(el){el.dataset.change=id;el.classList.add('proposal-zone');}return el;};
 const replace=(el,tag)=>{const next=doc.createElement(tag);for(const a of el.attributes)next.setAttribute(a.name,a.value);next.innerHTML=el.innerHTML;el.replaceWith(next);return next;};
 const button=(text,cls='btn btn--s')=>{const el=doc.createElement('button');el.type='button';el.className=cls;el.textContent=text;return el;};
 const actions=()=>{
   const form=card('Ваша работа')||card('Исправленная работа');
   const foot=form?.querySelector('.card__foot');if(!foot)return;
   const controls=[...foot.querySelectorAll('.btn')], caption=foot.querySelector('.caption');
   if(controls.length<2)return;
   foot.replaceChildren();foot.classList.add('proposal-submit');
   foot.append(controls[0]);
   const group=doc.createElement('div');group.className='proposal-precheck';group.append(controls[1]);if(caption)group.append(caption);foot.append(group);
   mark(foot,'submit-actions');
 };
 if(item.id==='navigation'){
   const nav=query('.aside .menu');const links=[...nav.children];
   links[2].textContent='Кабинет';links[3].remove();mark(nav,'reviewer-nav');
   const list=card('Активные');list.querySelector('h4').textContent='Мои работы';mark(list.querySelector('.card__head'),'reviewer-list');
 }
 if(item.id==='review'){
   const review=card('Предварительное ревью от модели');review.classList.add('proposal-review');review.closest('.row-rev').classList.add('proposal-review-layout');mark(review,'review-grid');
   const head=review.querySelector('.card__head');head.querySelector('.btn').remove();
   const sub=doc.createElement('span');sub.className='caption';sub.textContent='Баллы можно изменить перед отправкой решения';head.append(sub);
   review.querySelectorAll('.acc__h').forEach(row=>{
     if(row.querySelector('.scale'))row.classList.add('proposal-score-row');
     const pill=[...row.children].find(el=>el.classList.contains('pill'));const title=row.querySelector('.acc__t');
     if(pill&&title)title.append(pill);
   });
   const first=review.querySelector('.acc');first.querySelector('.acc__b').hidden=true;first.querySelector('.acc__chev').textContent='▼';
   const response=card('Ответ студенту');response.classList.add('proposal-response');mark(response,'review-response');
   const result=card('Результат');result.querySelector('.card__foot .btn-row').classList.add('proposal-decisions');mark(result,'review-response');
 }
 if(['submit','ai','resubmit'].includes(item.id)) actions();
 if(item.id==='submit'){
   const input=card('Ваша работа').querySelectorAll('input')[1];const field=input.closest('.field');const placeholder=input.placeholder;
   const area=doc.createElement('textarea');area.className='inp inp--area proposal-comment';area.rows=2;area.setAttribute('aria-label','Комментарий к сдаче, необязательно');
   input.replaceWith(area);const hint=doc.createElement('span');hint.className='field__hint';hint.textContent=placeholder;field.append(hint);mark(field,'submit-comment');
 }
 if(item.id==='ai'){
   const body=all('.acc__b').find(el=>el.textContent.includes('К отправке не готово.'));mark(body,'ai-advice');
   body.querySelector('b').textContent='Есть замечания перед отправкой.';
   body.querySelector('.callout p').textContent='Можно отправить работу сейчас или сначала внести правки. Решение принимает ревьюер.';
   body.querySelector('.caption').textContent='Доработайте работу и проверьте ссылку в форме ниже. Осталось проверок: 2 из 3.';
 }
 if(item.id==='resubmit'){
   const verdict=all('.kv').find(el=>el.firstElementChild?.textContent==='Вердикт');
   verdict.lastElementChild.textContent='Нужны правки';verdict.lastElementChild.style.color='var(--late)';mark(verdict,'resubmit-verdict');
   const form=card('Ваша работа');form.querySelector('h4').textContent='Исправленная работа';form.querySelector('.btn--pri').textContent='Отправить исправления';
 }
 if(item.id==='criteria'){
   const criteria=card('Критерии');criteria.classList.add('proposal-criteria');mark(criteria,'criterion-list');
   const body=criteria.querySelector('.card__body');const blocks=[...body.children].filter(el=>el.classList.contains('acc'));
   if(blocks.length!==9)throw new Error('К6 изменился: перед пересборкой проверить структуру девяти критериев.');
   blocks.forEach(block=>block.classList.add('proposal-criterion'));
   const expanded=blocks.find(block=>block.querySelector('.acc__b'));const detail=expanded.querySelector('.acc__b');
   const input=detail.querySelector('label.field input');const area=doc.createElement('textarea');
   area.className='inp inp--area';area.rows=3;area.textContent=input.value;input.replaceWith(area);mark(area.closest('.field'),'criterion-editor');
   const title=expanded.querySelector('.acc__h input');title.setAttribute('aria-label','Название критерия');title.classList.add('proposal-criterion-title');
   const order=doc.createElement('div');order.className='proposal-order';
   const number=doc.createElement('span');number.className='caption proposal-number';number.textContent='Критерий 1 из 9';order.append(number);
   for(const [text,direction] of [['Вверх','up'],['Вниз','down']]){const b=button(text);b.dataset.move=direction;b.setAttribute('aria-label',(direction==='up'?'Поднять':'Опустить')+' критерий');order.append(b);}
   detail.append(order);mark(order,'criterion-order');root.dataset.reorder='true';
 }
 if(item.id==='registry'){
   const tabs=query('.main > .tabs');const entries=[...tabs.querySelectorAll('.tab')];
   const controls=doc.createElement('div');controls.className='proposal-filters';
   const input=query('input[placeholder="Поиск по ID студента"]');input.classList.add('proposal-search');input.removeAttribute('style');input.setAttribute('aria-label','Поиск по ID студента');controls.append(input);
   const assignment=tabs.querySelector('.btn');assignment.removeAttribute('style');controls.append(assignment);
   const label=doc.createElement('label');label.className='proposal-status-filter';const text=doc.createElement('span');text.textContent='Статус';text.className='small';
   const select=doc.createElement('select');select.className='inp inp--s';select.setAttribute('aria-label','Статус работы');
   entries.forEach((entry,index)=>{const opt=doc.createElement('option');const count=entry.querySelector('.c').textContent;const name=entry.firstChild.textContent.trim();opt.textContent=`${name} · ${count}`;opt.value=index===0?'':name;select.append(opt);});
   label.append(text,select);controls.append(label);tabs.replaceWith(controls);mark(controls,'registry-filters');
   const caption=query('.main .card__head > .caption');caption.textContent='В макете показаны 8 из 88';caption.dataset.tableCount='true';root.dataset.registry='true';
 }
 if(item.id==='cohort'){
   const modal=query('.modal');const dates=[...modal.querySelectorAll('.field')].find(el=>el.firstElementChild?.textContent==='Даты');
   const inputs=[...dates.querySelectorAll('input')];dates.className='proposal-dates';dates.replaceChildren();
   inputs.forEach((input,i)=>{const label=doc.createElement('label');label.className='field';const text=doc.createElement('span');text.className='field__lbl';text.textContent=i===0?'Дата начала':'Дата окончания';label.append(text,input);dates.append(label);});mark(dates,'cohort-dates');
   const id=modal.querySelector('input[disabled]');const value=doc.createElement('div');value.className='proposal-readonly mono';value.textContent=id.value;id.replaceWith(value);mark(value.closest('.field'),'cohort-id');
 }
 if(item.id==='history'){
   const log=card('История решений');log.classList.add('proposal-history');mark(log,'history-log');
   log.querySelectorAll('.kv').forEach(row=>{row.classList.add('proposal-event');row.lastElementChild.classList.add('caption');});
 }
 if(item.id==='export'){
   const modal=query('.modal');modal.classList.add('proposal-export');mark(modal,'export-width');
   const input=modal.querySelector('input.inp');const value=doc.createElement('div');value.className='proposal-export-context';value.textContent=input.value;input.replaceWith(value);mark(value.closest('.field'),'export-context');
 }
 // These are real controls only where the proposal demonstrates interaction.
 all('.proposal-review .scale').forEach(group=>{group.setAttribute('role','group');group.setAttribute('aria-label','Балл: '+group.closest('.acc__h').querySelector('.acc__t').textContent);});
 all('.proposal-review .scale > span').forEach(el=>{const b=replace(el,'button');b.type='button';b.setAttribute('aria-pressed',String(b.classList.contains('is-on')));});
 all('span.btn').forEach(el=>{const b=replace(el,'button');b.type='button';});
 return root.innerHTML;
}

// This function is embedded into the sandboxed preview frame.
function frameRuntime(){
 const report=()=>parent.postMessage({type:'audit-height',height:Math.ceil(document.body.getBoundingClientRect().height)+4},'*');
 document.fonts.ready.then(report);window.addEventListener('load',report);new ResizeObserver(report).observe(document.body);
 let notice;
 function localNotice(text){if(!notice){notice=document.createElement('div');notice.className='prototype-notice';notice.setAttribute('role','status');document.body.append(notice);}notice.textContent=text;notice.hidden=false;clearTimeout(localNotice.timer);localNotice.timer=setTimeout(()=>notice.hidden=true,3500);}
 document.addEventListener('click',event=>{
  const move=event.target.closest('[data-move]');
  if(move){const block=move.closest('.proposal-criterion');const sibling=move.dataset.move==='up'?block.previousElementSibling:block.nextElementSibling;if(sibling?.classList.contains('proposal-criterion')){if(move.dataset.move==='up')sibling.before(block);else sibling.after(block);[...document.querySelectorAll('.proposal-criterion')].forEach((el,i)=>{const number=el.querySelector('.proposal-number');if(number)number.textContent=`Критерий ${i+1} из 9`;el.querySelectorAll('[data-move]').forEach(b=>b.setAttribute('aria-label',`${b.dataset.move==='up'?'Поднять':'Опустить'} критерий ${i+1}`));});move.focus();}return;}
  const scale=event.target.closest('.proposal-review .scale button');
  if(scale){if(scale.closest('[data-custom]')){localNotice('Макет: влияние своего требования на максимум баллов ещё не определено.');return;}const group=scale.parentElement;[...group.children].forEach(b=>{b.classList.toggle('is-on',b===scale);b.setAttribute('aria-pressed',String(b===scale));});const n=s=>Number(s.replace(',','.'));const value=n(scale.textContent);const max=Math.max(...[...group.children].map(b=>n(b.textContent)));group.classList.toggle('scale--zero',value===0);group.classList.toggle('scale--max',value===max);const marker=group.closest('.acc__h').querySelector('.ck');if(marker&&!marker.classList.contains('ck--h')&&!marker.classList.contains('ck--q')){marker.classList.toggle('ck--y',value===max);marker.classList.toggle('ck--n',value<max);marker.textContent=value===max?'✓':'✕';}const total=[...document.querySelectorAll('.proposal-review .acc:not([data-custom]) .scale > .is-on')].reduce((sum,b)=>sum+n(b.textContent),0);const result=[...document.querySelectorAll('.card')].find(c=>c.querySelector('h4')?.textContent==='Результат');if(result){const rows=result.querySelectorAll('.card__body .kv');rows[0].lastElementChild.textContent=total.toLocaleString('ru-RU');rows[2].lastElementChild.innerHTML=`${(total-1).toLocaleString('ru-RU')}<span style="color:var(--ink-3);font-weight:400"> из 6</span>`;rows[2].lastElementChild.style.color=total-1<4?'var(--bad)':'var(--ink)';result.querySelector('.card__foot > div').style.color=total-1<4?'var(--bad)':'var(--ink-2)';result.querySelector('.card__foot > div').textContent=total-1<4?'Ниже порога зачёта, нужно 4 из 6. Выберите, что делать дальше.':'Порог зачёта пройден: 4 из 6.';}const summary=document.querySelector('.proposal-review .card__body > .kv:last-child');if(summary)summary.lastElementChild.textContent=`${total.toLocaleString('ru-RU')} из 6`;return;}
  const row=event.target.closest('.proposal-review .acc__h');
  if(row&&!event.target.closest('button')){const body=row.nextElementSibling;if(body?.classList.contains('acc__b')){body.hidden=!body.hidden;const c=row.querySelector('.acc__chev');if(c)c.textContent=body.hidden?'▼':'▲';report();}return;}
  const control=event.target.closest('button,a');if(control){event.preventDefault();localNotice('Макет: это действие не отправляет и не сохраняет данные.');}
 });
 if(document.body.dataset.registry){const input=document.querySelector('.proposal-search');const select=document.querySelector('.proposal-status-filter select');const rows=[...document.querySelectorAll('.main tbody tr')];const update=()=>{let count=0;rows.forEach(row=>{const status=row.querySelector('.st')?.textContent.trim();row.hidden=!(row.firstElementChild.textContent.includes(input.value.trim())&&(!select.value||status===select.value));if(!row.hidden)count++;});document.querySelector('[data-table-count]').textContent=`В макете: ${count} из 8 строк. Всего в потоке — 88.`;report();};input.addEventListener('input',update);select.addEventListener('change',update);}
 window.addEventListener('message',event=>{if(event.source!==parent)return;if(event.data.type==='audit-highlight'){document.body.classList.toggle('show-changes',event.data.show);}if(event.data.type==='audit-find'){const element=document.querySelector(`[data-change="${event.data.id}"]`);if(element)parent.postMessage({type:'audit-focus',y:element.getBoundingClientRect().top+window.scrollY},'*');}});
 // Keep custom added criteria outside the configured six-point total.
 document.querySelectorAll('.proposal-review .acc').forEach(el=>{if(el.textContent.includes('Своё требование'))el.dataset.custom='true';});
 report();
}
