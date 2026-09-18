#!/usr/bin/env python3
"""Build the Hebrew review from preserved evidence. Never edits v1.0."""
from pathlib import Path
import html
import json
import re
import statistics

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
REPORTS = REPO / 'docs/reports'
STEM = 'vos-performance-failure-2026-09-18-v1.1'
data = json.loads((HERE / 'measured-summary.json').read_text())
runs = data['runs']

def esc(s):
    return html.escape(str(s))

def fmt(x):
    return f'{x:,.1f}' if isinstance(x, float) and not x.is_integer() else f'{int(x):,}'

def code(s):
    return '<code>' + esc(s) + '</code>'

def table(head, rows, cls=''):
    return '<table class="' + cls + '"><thead><tr>' + ''.join('<th>' + h + '</th>' for h in head) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + str(v) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table>'

def paragraph(s, cls=''):
    return '<p class="' + cls + '">' + s + '</p>'

def selected(pattern):
    return [r for r in runs if re.fullmatch(pattern, r['run'])]

groups = [
    ('E1/E2 · A · סוללה', 'e12-a-[1-5]'),
    ('E1/E2 · B · סוללה', 'e12-b-[1-5]'),
    ('E1/E2 · A · AC', 'e12-ac-a-[1-5]'),
    ('E1/E2 · B · AC — חלקי', 'e12-ac-b-[1245]'),
    ('E4 · בקרה · סוללה — חקר', 'e4-control-[1-4]'),
    ('E4 · yield · סוללה — חקר', 'e4-yield-[1-4]'),
]
rows = []
for label, pattern in groups:
    rr = selected(pattern)
    values = [r['health'][0]['rate'] for r in rr]
    rows.append([label, len(values), '<span class="numbers">' + ' / '.join(fmt(x) for x in values) + '</span>', fmt(statistics.median(values)), fmt(min(values)) + '–' + fmt(max(values))])

CSS = '''
@page{size:A4;margin:15mm 16mm 16mm}
*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.45;color:#19313d;direction:rtl}
.page{break-after:page}.page:last-child{break-after:auto}.mast{border-bottom:2px solid #247f85;padding-bottom:7px;color:#526b77;font-size:9pt}.brand{float:left;direction:ltr;color:#247f85;font-weight:bold}
h1{font-size:27pt;line-height:1.2;margin:17px 0 10px}h2{font-size:16pt;color:#155f68;margin:15px 0 7px}h3{font-size:12pt;margin:10px 0 5px}p{margin:7px 0}.lead{font-size:12.5pt}.note{font-size:9pt;color:#4c626d}
.notice{padding:10px 12px;background:#fff2eb;border-right:4px solid #b55435;margin:12px 0}.info{padding:9px 12px;background:#edf5f6;border-right:4px solid #247f85;margin:10px 0}
.cards{display:flex;gap:9px;margin:15px 0}.card{flex:1;text-align:center;background:#edf3f5;padding:10px 5px;border-radius:4px;font-size:9pt}.big{display:block;font-size:19pt;font-weight:bold;direction:ltr;line-height:1.3}.status{font-weight:bold;color:#a13e2d}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:9pt;line-height:1.4}th{background:#e8f1f3;text-align:right;padding:7px 6px}td{border-bottom:1px solid #d9e3e6;padding:7px 6px;vertical-align:top}tr{break-inside:avoid}.dense{font-size:8.2pt}.dense td{padding:5px}.numbers{direction:ltr;unicode-bidi:embed;display:inline-block;font-variant-numeric:tabular-nums}
code{direction:ltr;unicode-bidi:embed;display:inline;font-family:monospace;font-size:.88em;overflow-wrap:anywhere}.hash{display:block;direction:ltr;text-align:left;font:8.5pt/1.4 monospace;background:#edf3f5;padding:7px;overflow-wrap:anywhere;margin:5px 0 9px}
.formula{direction:ltr;text-align:center;font-family:monospace;background:#edf3f5;padding:9px;margin:9px 0}.foot{border-top:1px solid #d9e3e6;margin-top:13px;padding-top:6px;font-size:8.5pt;color:#5b717b}
a{color:#176979;text-decoration:none}.qa{break-inside:avoid;margin:10px 0 14px;border-right:2px solid #bdd7da;padding-right:10px}.qa p{font-size:9.2pt;margin:5px 0}.qa strong{color:#155f68}.appendix h2{break-after:avoid}.appendix{break-before:page}.appendix .mast{margin-bottom:12px}ul{padding-right:19px}li{margin:5px 0}
'''

pages = []
def page(title, content, n):
    pages.append('<section class="page"><div class="mast"><span class="brand">vOS · Review 1.1</span>18 בספטמבר 2026 · אבחון SPSC</div><h1>' + title + '</h1>' + content + '<div class="foot">גרסה 1.1 · מצב האבחון בזמן הפקת הדוח · עמוד סיכום ' + str(n) + ' מתוך 6</div></section>')

historical = [
    ['A — ביקורת BIOS', '56/57', '41,556', '1,010', '41,144'],
    ['תיקון ביניים — BIOS', '56/57', '41,465', '1,010', '41,054'],
    ['B — BIOS', '56/57', '41,931', '1,000', '41,931'],
    ['B — UEFI', '56/57', '43,279', '1,010', '42,850'],
]
page('אותו ISO נכשל ועבר<br>בתנאי ריצה שונים',
     paragraph('הממצא המרכזי: כשל הביצועים אינו מוסבר לבדו בתיקון SYSINFO. אותו קובץ ISO נכשל בסוללה ועבר בריצות AC באותו יום. נצפה קשר לשינוי תנאי המארח, אך גורם יחיד ומנגנון התזמון עדיין לא בודדו.', 'lead') +
     '<div class="notice"><b>הכשל נשאר פתוח.</b> סדרת AC אינה שלמה לכל תצורה. E3 נחסם בכשל מקדים; E4 חלקי; E5–E8 הוכנו ולא הורצו. אין אישור שחרור או טענה שהקוד תוקן.</div>' +
     '<div class="cards"><div class="card"><span class="big">80,000</span>סף מקורי, הודעות/שנייה</div><div class="card"><span class="big">86,575–90,635</span>17.9 · ארבע ריצות</div><div class="card"><span class="big">41,054–42,850</span>18.9 · ארבע ריצות הדוח המקורי</div></div>' +
     '<h2>הראיות המקוריות נשמרו</h2>' + table(['תמונה ומסלול', 'עברו', 'נצרכו', 'זמן אורח, ms', 'הודעות/שנייה'], historical) +
     paragraph('ב־17.9 עברו ארבע סוויטות של A: ‏86,575 / 90,635 / 88,460 / 87,786. אלו שתי ריצות BIOS ושתי UEFI, ולא ארבע חזרות באותה תצורה. בארבע שורות 18.9: n=1 לכל תצורה.') +
     '<h2>איזו תוכנית נכשלה</h2>' +
     paragraph(code('test_health_check') + ' היא התוכנית ה־51 מתוך 57, והחזירה ' + code('exit=1') + '. ' + code('bench_ai_throughput') + ' היא ה־43 ויצאה באפס. לא נמצא סמן FAIL בתחום תוכנית שיצאה באפס בשמונת הלוגים ההיסטוריים או ב־31 הלוגים החדשים.') +
     paragraph('הודעת ה־validator הכללית מתייחסת כאן ליציאה שאינה אפס. לא נמצאו חסר, כפילות או שינוי סדר בסוויטות שהושלמו. בדיקת הקבלה כוללת גם תוכן לוג וסדר תוכניות.', 'note') +
     paragraph('היקף: x86-64 ב־QEMU TCG, ‏vCPU יחיד, 3,072 MiB. הסף, חלון 1,000ms וקיבולת 1,024 לא שונו בקוד הקנוני. הבדיקות החדשות הן BIOS בלבד.', 'note'), 1)

page('המודל הכמותי והסתייגויות',
     '<h2>פרק זמן שקול לקיבולת — לא זמן מחזור שנמדד</h2>' +
     table(['יום', 'הודעות/שנייה', 'קיבולות שקולות/שנייה', 'זמן שקול ל־1,024'], [
         ['17.9', '86,575–90,635', '84.5–88.5', '11.3–11.8 ms'],
         ['18.9 — המקור', '41,054–42,850', '40.1–41.8', '23.9–24.9 ms'],
     ]) + '<div class="formula">capacity-equivalent interval = 1,024 × 1,000 / rate [ms]</div>' +
     paragraph('נוסף פרק זמן שקול של כ־12–14ms. זה מתיישב עם השערת רגישות לטיק של 10ms, אך הטבעת יכולה להתמלא ולהתרוקן חלקית. חילוק הקצב בקיבולת אינו מדידה של fill/drain, ואינו מוכיח מספר שלם של טיקים למחזור.') +
     '<h2>GETPID אינו ההסבר היחיד</h2>' +
     paragraph('בדוגמה ההיסטורית ממוצע GETPID עלה מ־8,317 ל־11,899–12,170 יחידות TSC, פי 1.43–1.46. מודל ליניארי גס היה מנבא כ־61 אלף במקום כ־41 אלף. החישוב הוא השוואה בין מדדים שונים, ולא מודל עלות מלא של SPSC.') +
     paragraph('בכל מדידת GETPID יש 100 חזרות חימום ו־10,000 דגימות; הלוג הקנוני שומר מינימום, ממוצע ומקסימום בלבד. אין ממנו חציון או p99. לכל ערך ממוצע היסטורי הנזכר כאן ריצה אחת; אין לפרש אותו כממוצע בין ריצות.') +
     paragraph('מילוי 64MB ב־xorshift, ללא syscall בתוך האזור הנמדד, עלה באותו ISO A מ־28,476,000 ו־28,509,000 (BIOS, n=2) ל־43,715,000 (n=1): פי 1.533–1.535. זו לולאת חישוב וזיכרון, לא ALU מבודד. היא סותרת הסבר המוגבל למסלול syscall, אך אינה מזהה לבדה את המארח כסיבה.') +
     '<h2>עמדה ביחס לחוות דעת היועץ</h2>' +
     paragraph('מסכים עם הצורך בתיעוד חשמל, מדגם חוזר, סטטוס ABI נפרד ומכשור. חולק על קביעות חד־משמעיות: תגובת מדרגות אינה מאשרת לבדה את השערת הטיק; שוויון תחת icount אינו שולל כל רגרסיית קוד; 0.22% בין שתי ריצות בודדות אינו ראיית שקילות.') +
     paragraph('תיקון מפורש לביקורת שלי: שדה quantum מרובה טיקים אינו שולל החלפה בכל טיק. עקיבת הקוד מצאה שה־BSP מבקש reschedule בכל IRQ טיימר, ללא תלות ביתרת הפרוסה. נדרשת E7 לתצפית על הבחירות בפועל.') +
     paragraph('היצרן בודק GETTIME בכל ניסיון ודורש yield כשהטבעת מלאה; הצרכן מסתובב כשהיא ריקה. push/pop עצמם פועלים בזיכרון משותף. המספר הכולל משלב עלות קריאות מערכת ותזמון; הוא אינו מדד latency עצמאי.', 'note'), 2)

page('כל תוצאות החזרות החדשות',
     paragraph('A הוא ה־ISO הישן; B כולל את תיקון SYSINFO. אין איגום של סוללה ו־AC. ״יציב״ כאן פירושו זהות בדגימות לפני ואחרי בלבד; אין ניטור רציף שמוכיח שלא היה מעבר קצר באמצע.', 'note') +
     table(['תצורה', 'n', 'כל התוצאות, הודעות/שנייה', 'חציון', 'מינימום–מקסימום'], rows, 'dense') +
     paragraph('E1/E2 בסוללה: 10/10 סוויטות נכשלו ב־health בלבד. ב־AC ללא שינוי מתועד בקצה: A ‏5/5 ו־B ‏4/4 עברו 57/57. ‏B טרם עומד בדרישת חמש חזרות; אין מסקנת שקילות A/B. ב־E4 ארבע גרסאות yield עברו את הסף האבחוני מול ארבע בקרות שנכשלו, אך זה מדגם חקר n=4 ואינו שער קבלה.') +
     '<h2>ריצות שינוי תנאים — נשמרו בנפרד</h2>' +
     table(['ריצה', 'השינוי', 'קצב', 'תוצאה'], [
         [code('e4-control-5'), 'סוללה → AC', '102,400', '57/57'],
         [code('e12-ac-b-3'), 'AC → סוללה', '42,409', '56/57'],
         [code('e12-ac-a-6'), 'AC נשאר; זמן כיבוי מסך שונה ל־180 דקות', '100,284', '57/57'],
     ], 'dense') +
     paragraph('בכל שלוש הריצות גיבובי QEMU וה־ISO נשארו זהים. אלה אינן תוצאות שנמחקו, ואינן כלולות במדגם התנאים היציבים. הזוג הבא נעצר בבדיקת קדם כאשר המחשב חזר לסוללה; לא הופעל עבורו VM.') +
     paragraph('חציון מילוי 64MB ב־A: סוללה 42,469,000; ‏AC ‏27,275,000 יחידות TSC, יחס 1.56. חציון SPSC השתנה מ־40,696 ל־99,450, יחס 2.44. האטה לא־ליניארית תואמת אינטראקציה בין מהירות הביצוע לתזמון, אך E7 עדיין דרושה להכרעה.') +
     paragraph('81,384 ו־87,971 נשמרו בתוך קבוצת AC. אין בסיס לטעון ששתי רמות קשיחות בלבד קיימות. כל הספירות, GETPID ומדדי החישוב לכל ריצה נמצאים ב־measured-summary.json ובנספח התוצאות.', 'note'), 3)

page('מצב הניסויים וסביבת המארח',
     table(['ניסוי', 'מה בוצע / מה נותר', 'מצב'], [
         ['E1', 'מדד חישוב וזיכרון קנוני, ללא syscall פנימי; חזרות בתוך E2.', 'בוצע; לא ALU מבודד'],
         ['E2', 'סוללה: 5 לכל ISO; AC: ‏A=5, ‏B=4 בתנאי קצה ללא שינוי.', 'מוגבל; מארח שקט כולו לא אומת'],
         ['E3', code('-icount shift=3,sleep=off') + ': ניסיון A אחד, timeout ‏500s, אפס תוצאות SPSC.', 'חסום בתנאי קדם'],
         ['E4', '4 זוגות בסוללה. גרסת yield מסומנת ונפרדת; דרוש מדגם של 5 בתנאים מתואמים.', 'חקר חלקי'],
         ['E5', '512/2,048: שני ISO נבנו. דרושות 5 חזרות לכל קיבולת.', 'לא הורץ'],
         ['E6', 'health מוקדם, GETPID לפני clone וספירת runnable; ISO נבנה.', 'לא הורץ'],
         ['E7', 'מונה IRQ, סיבות החלפה, זהויות ומעברי טבעת; ISO נבנה. נדרשת בקרה ללא מכשור.', 'לא הורץ'],
         ['E8', '10,000 דגימות מוקדם/מאוחר, מכשור כבוי/פעיל ופירוק שלבים; ISO נבנה.', 'לא הורץ'],
     ], 'dense') +
     '<div class="notice"><b>ממצא E3 נפרד:</b> ' + code('test_model_load') + ' יצר 16 workers, השלים 0/16 ו־190/992 קבצים לפני deadline של 30,000ms אורח. הבדיקה חזרה בלי join/cancel. מסלול exit_group מסיים רק את הקורא; משימות עשויות להישאר. אין הוכחת UAF ואין תוצאת SPSC. לא הארכנו את חלון הבדיקה כדי להסתיר את הכשל.</div>' +
     '<h2>מה נשמר בכל ריצה</h2>' +
     paragraph('פקודת QEMU מלאה, גיבובי בינארי ו־ISO, עומס, רשימת תהליכים, חשמל, פרופילי הספק ו־pmset therm לפני/אחרי. המארח: Apple M4 Pro, ‏Mac16,8, ‏14 מעבדים לוגיים, macOS ‏26.3.1 ‏(25D2128); ‏QEMU ‏10.2.2.') +
     paragraph('בפרופילים: סוללה powermode=1, ‏AC powermode=0. לא נמדדו תדרים, מיקום P/E או QoS. היעדר אזהרת thermal אינו מדידת טמפרטורה או הוכחה שאין throttling. יישומי משתמש ברקע נשארו פעילים; בזמן VM לא התבצעה עבודת סוכנים נוספת.') +
     paragraph('המשך מותנה בתיאום מקור חשמל קבוע. לאחר מכן יש להשלים את המדגם החסר ולרוץ E4→E8 לפי הרישום המוקדם. עשר ריצות מלאות דורשות כ־20–30 דקות VM; E6 צפוי להיות קצר יותר. זמן הכשרה ושחזור תוצאות מכשור נוסף לכך. הגרסאות האבחוניות אינן נחשבות לשער.', 'note'), 4)

page('אבטחה, ABI ותנאי סגירה',
     table(['פריט', 'סטטוס מפורש'], [
         ['ביצועים SPSC', '<b class="status">פתוח.</b> רגישות לתנאי ריצה נצפתה; מנגנון וסביבת קבלה טרם הוכרעו.'],
         ['תיקון SYSINFO', 'מחויב מקומית ב־0324521; אומת בתחום בדיקות ABI/host והרצות TCG עם vCPU יחיד. אין כאן אישור merge מרוחק או שחרור.'],
         ['FAIL עם exit=0', 'לא נמצא בתחום 8 הלוגים ההיסטוריים ו־31 החדשים שנבדקו. ההשערה אינה ממצא תקלה.'],
         ['VFS / exit_group תחת icount', 'ממצא פתוח נפרד; תנאי הקדם ל־E3 נכשל.'],
     ]) +
     paragraph('הקריאה החדשה 483 מקבלת מצביע, גודל וגרסה; המבנה 40 בתים. קריאה 99 מחזירה ENOSYS בלי כתיבה. 15 הצהרות native הועברו לחוזה המשותף; musl מפיץ כשל. EFAULT עשוי להשאיר קידומת כתובה: אין הבטחת הכול־או־כלום.') +
     paragraph('110 בדיקות host מהראיות הקודמות אינן סוגרות שער ביצועים. זהות כתובת sysinfo ו־__lsysinfo נבדקה בבינארי השמור; לא נמצאה בדיקת CI ייעודית שמחייבת זהות כתובות בעתיד. ביקורת גורפת למסלולי copyout סמוכים טרם הושלמה.') +
     '<h2>היקף NPU</h2>' +
     paragraph('החומרה דווחה ' + code('[UNAVAILABLE] ... no authorized BAR registry') + '. בדיקות דחייה בטוחה ותוכנית המעטפת יכולות לצאת באפס; אין בכך כיסוי NPU פיזי. זו אינה תוכנית שלמה שנספרה כדילוג במסווג.') +
     '<h2>להחלטת CTO — ללא המצאת אישורים</h2>' +
     paragraph('נדרשים בעל אחריות ומועד לאבחון; מספר מעברים רצופים ומעטפת מארח/BIOS/UEFI מחייבת; החלטה על סביבת TCG דטרמיניסטית או חומרה ייעודית; סמכות לשינוי שער; מיפוי מזהי הסוגיות למסעות J0–J12 ול־REL/CAP. לא נמצא במאגר מיפוי מוסמך שמאפשר למלא פרטים אלה כעובדה.') +
     paragraph('עד להחלטה וראיות קבלה, אין הכרזת סגירת כשל או שחרור. הסף 80,000 נשמר. יש לחדש קבלה קנונית חוזרת ב־BIOS וב־UEFI לאחר האבחון; ריצות אבחון, בנייה ובדיקות host אינן תחליף.') +
     paragraph('מותר להמשיך סקירות, תיקוני בטיחות ותיעוד בין חלונות המדידה. SMP, חומרה פיזית, מרוצי unmap/mprotect מול usercopy ובידוד מלא נשארו מחוץ לאימות הנוכחי.', 'note'), 5)

page('שרשרת ראיות ושינויים ב־1.1',
     '<h2>מזהה מקור יחיד לכל תמונה קנונית</h2>' +
     paragraph('A — מקורות הבנייה תואמים ל־commit:') + '<span class="hash">cfd23f6a8595b13da005c59f1efacb6744e53832</span>' +
     paragraph('SHA-256 של ISO A:') + '<span class="hash">a473dfabbbc0bb10973327a256febd809d1399cd8efc6766d25086987827ba05</span>' +
     paragraph('B — מקורות הבנייה תואמים ל־commit:') + '<span class="hash">0324521a2494b25c29a01b055ff8b38029819fb9</span>' +
     paragraph('SHA-256 של ISO B:') + '<span class="hash">90cc28eeef42290b10fa0a4f4bc443bed40dae54358bcb7a9127fbe499378a64</span>' +
     paragraph('זו התאמה רטרוספקטיבית של תוכן, ולא טענה שהבנייה ההיסטורית הופעלה מעץ Git נקי. ב־A כל 3,689 פריטי תחום הבנייה תואמים; יש הבדל במסמך אחד מתוך 6,321 פריטי manifest. ב־B כל 6,392 הפריטים תואמים. זהות modes, toolchain וכל קלט בלתי מוצהר לא הוכחה.', 'note') +
     paragraph('QEMU SHA-256 בכל המדידות החדשות:') + '<span class="hash">44a9a411a38b9664954fbb1d197ec003fd4c666075112166e5fc1f94a29f399a</span>' +
     '<h2>רשימת שינויים מול v1.0</h2>' +
     paragraph('נוספו T1–T5 וכל תשובות הוועדה; טבלת הזמן השקול והסתייגויות; זיהוי health וקוד היציאה; n ופיזור; סטטוס SYSINFO נפרד; NPU; מיפוי commit; תיעוד ניסויים ומעברי חשמל. כרטיס בדיקות ה־host הוחלף בטווח 17.9. הטענה של 0.22% סויגה, ותוקנה הסקת quantum החלקית שלי.') +
     paragraph('קובצי v1.0 HTML/PDF נשמרו ללא שינוי. כל כשל, timeout וריצה בתנאים משתנים נשמרו. נספח השאלות להלן כולל תשובות, ראיות, ביטחון והשלמות נדרשות. פירוט כל ריצה ופקודת השחזור נמצא בחבילה:') +
     '<span class="hash">docs/design/evidence/spsc-council-2026-09-18/</span>' +
     paragraph('מקורות מרכזיים: ' + code('triage-before-experiments.md') + ', ' + code('preregistration.md') + ', ' + code('measured-summary.json') + ', ' + code('validator-all-runs.json') + ', ' + code('source-commit-mapping.md') + ', ' + code('diagnostics/artifacts.json') + '.', 'note'), 6)

# One canonical answer block per requested ID; keep the dated pre-experiment file intact.
inputs = ['answers-triage-he.md', 'answers-skm-he.md', 'answers-qh-he.md', 'answers-vag-he.md']
answers = {}
for name in inputs:
    text = (HERE / name).read_text()
    starts = list(re.finditer(r'^\[([TSKQHMVAG]\d+)\]\s*תשובה:', text, re.M))
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[match.start():end].strip()
        # Supplementary headings and narrative updates are separate evidence, not part of the final answer.
        block = re.split(r'\n## |\n\nעדכון לאחר הניסויים', block)[0].strip()
        ident = match.group(1)
        if ident in answers:
            raise ValueError('Duplicate answer ' + ident)
        if 'ראיה:' not in block or 'ביטחון:' not in block:
            raise ValueError('Missing required format ' + ident)
        answers[ident] = block
order = [(c, n) for c, n in [('T', 5), ('S', 7), ('K', 6), ('Q', 6), ('H', 6), ('M', 6), ('V', 6), ('A', 6), ('G', 6)]]
expected = [f'{c}{i}' for c, n in order for i in range(1, n + 1)]
assert set(answers) == set(expected), (set(expected) - set(answers), set(answers) - set(expected))
labels = dict(T='מיון מוקדם', S='מתזמן וטיימר', K='קריאות מערכת וזיכרון', Q='אמולציה', H='סביבת מארח', M='מדידה וסטטיסטיקה', V='תשתית אימות', A='אבטחה ו־ABI', G='שחרור וממשל')

def inline(s):
    s = esc(s)
    def link(match):
        href = match.group(2)
        if not href.startswith(('https://', 'http://', '/')):
            href = '../design/evidence/spsc-council-2026-09-18/' + href
        return '<a href="' + href + '">' + match.group(1) + '</a>'
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    s = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', s)
    return s

md = '# vOS — תשובות מלאות לוועדה · v1.1 · 18.9.2026\n\n'
md += 'מצב: האבחון חלקי, כשל SPSC פתוח. T1–T5 נשמרות כתשובות שנכתבו לפני ניסוי; התוצאות המאוחרות מפורטות בשאלות המקצועיות ובדוח. תנאי מארח משתנים עצרו את המשך המדידות. כל 54 השאלות נענות; תשובה ״דורש ניסוי״ אינה ניסוי שהושלם.\n\n'
md += 'נתיבי קוד יחסיים לשורש המאגר; קובצי הוועדה והריצות יחסיים לתיקייה זו. חבילות health-performance ו־sysinfo-abi הן תיקיות אחיות תחת docs/design/evidence. כל המקורות והלוגים נשמרים; סטטוס זה אינו אישור קבלה.\n\n'
appendices = []
for c, n in order:
    md += '## ' + labels[c] + '\n\n'
    section = '<section class="appendix"><div class="mast"><span class="brand">vOS · Committee Answers</span>נספח · תשובות, ראיות ורמת ביטחון</div><h2>' + c + ' — ' + labels[c] + '</h2>'
    if c == 'T':
        section += paragraph('תשובות המיון נכתבו לפני הניסויים ונשמרות בנוסחן. עדכוני הניסויים נמצאים בגוף הדוח ובתשובות S–G. לא ידוע היסטורי אינו הופך לידוע בעקבות בניית ISO אבחוני.', 'note')
    for i in range(1, n + 1):
        block = answers[f'{c}{i}']
        md += block + '\n\n'
        section += '<div class="qa">' + ''.join('<p>' + inline(line) + '</p>' for line in block.splitlines() if line.strip()) + '</div>'
    appendices.append(section + '</section>')
(HERE / 'committee-answers-v1.1-he.md').write_text(md)

# Raw individual values in one compact appendix; missing SPSC remains a dash.
runrows = []
for r in runs:
    health = r['health']
    state = r['power_class'] + ('; settings changed' if r.get('power_settings_changed') else '')
    rate = fmt(health[0]['rate']) if len(health) == 1 else '—'
    fill = ' / '.join(fmt(x) for x in r['compute_fill_tsc']) or '—'
    gp = ' / '.join(f"{fmt(x['min'])}, {fmt(x['avg'])}, {fmt(x['max'])}" for x in r['getpid_tsc']) or '—'
    runrows.append([code(r['run']), esc(state), rate, fill, '<span class="numbers">' + gp + '</span>'])
appendices.append('<section class="appendix"><h2>כל הריצות — ערכים בודדים</h2>' + paragraph('GETPID: מינימום, ממוצע, מקסימום מתוך 10,000 דגימות בכל ריצה. אין דגימות גולמיות שמאפשרות p99. גם כשל E3 וכל ריצות שינוי התנאים נשמרים כאן. יחידות זמן הן TSC מדווח, לא מחזורי CPU פיזיים מכוילים.', 'note') + table(['מזהה ריצה', 'תנאי קצה', 'SPSC', 'מילוי 64MB', 'GETPID: min, avg, max'], runrows, 'dense') + '</section>')
out = '<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><title>vOS — אבחון SPSC — v1.1</title><style>' + CSS + '</style></head><body>' + ''.join(pages + appendices) + '</body></html>'
(REPORTS / (STEM + '.html')).write_text(out)
print(json.dumps({'html': str(REPORTS / (STEM + '.html')), 'answers': len(answers), 'runs': len(runs)}, ensure_ascii=False))
