from pathlib import Path
import hashlib, html, json, re

REPO=Path('/Users/sz/Desktop/vos/vos 5')
BASE=Path('/private/tmp/vos-sysinfo-qualified-20260918')
OUT=REPO/'docs/reports/vos-performance-failure-2026-09-18.html'

def case(label, folder):
    folder=Path(folder)
    p=folder/'result.json'
    if not p.exists(): return {'label':label,'pending':True}
    d=json.loads(p.read_text())
    text=(folder/'serial.log').read_text(errors='replace').replace('\x00','').replace('\r','')
    exits=re.findall(r'^=== ([^\n:]+): exit=(-?\d+) ===',text,re.M)
    h=text[text.index('--- Check 4: spsc_responsive ---'):]
    def number(key): return int(re.search(r'^\s*'+key+r':\s*(\d+)',h,re.M).group(1))
    data={'label':label,'pending':False,'pushed':number('pushed'),'consumed':number('consumed'),
          'elapsed_ms':number('elapsed'),'rate':number('throughput'),
          'zero_exits':sum(x[1]=='0' for x in exits),'exits':len(exits),'passed':d['passed'],
          'iso_sha256':d['iso_sha256'],
          'sysinfo_native':'[PASS] sysinfo_versioned_abi' in text,
          'sysinfo_musl':'[PASS] test_env: sysinfo_errno_no_write' in text}
    assert data['rate']==data['consumed']*1000//data['elapsed_ms']
    assert data['exits']==57 and len(set(x[0] for x in exits))==57
    return data

cases=[case('ISO קודם — ריצת ביקורת, BIOS','/private/tmp/vos-sysinfo-control-20260918'),
       case('תיקון ABI — גרסת ביניים, BIOS','/private/tmp/vos-sysinfo-final-20260918/bios'),
       case('מקורות סופיים — BIOS',BASE/'bios'),case('מקורות סופיים — UEFI',BASE/'uefi')]
manifest=json.loads((BASE/'source-manifest.json').read_text())
rows=''
for c in cases:
    if c['pending']:
        rows+=f'<tr><td>{c["label"]}</td><td colspan="4">ממתין לסיום הריצה</td></tr>'
    else:
        rows+=f'<tr><td>{c["label"]}</td><td>{c["zero_exits"]}/57</td><td>{c["consumed"]:,}</td><td>{c["elapsed_ms"]:,}</td><td class="bad">{c["rate"]:,}</td></tr>'
base_rate=cases[0]['rate'];abi_rate=cases[1]['rate']
relative=abs(abi_rate-base_rate)*100/base_rate
report_data={'date':'2026-09-18','base_commit':manifest['commit'],
             'source_patch_sha256':manifest['dirty_diff_sha256'],
             'source_manifest_sha256':hashlib.sha256((BASE/'source-manifest.json').read_bytes()).hexdigest(),
             'cases':cases,'threshold':80000,'host_tests':110,
             'historical_rates':[86575,90635,88460,87786],
             'scope':'QEMU TCG x86_64, one vCPU, 3072 MiB, BIOS and UEFI; physical hardware unqualified'}
(BASE/'failure-report-data.json').write_text(json.dumps(report_data,indent=2,ensure_ascii=False)+'\n')
iso=hashlib.sha256((BASE/'source/dist/final-full.iso').read_bytes()).hexdigest()
old_iso=cases[0]['iso_sha256']
css='''
@page{size:A4;margin:16mm 17mm}*{box-sizing:border-box}body{margin:0;color:#172b3a;font-family:Arial,sans-serif;font-size:11.4pt;line-height:1.55;direction:rtl;background:white}section{break-after:page;position:relative;min-height:260mm}section:last-child{break-after:auto}.kicker{font-size:10pt;color:#557181;letter-spacing:.3px;border-bottom:2px solid #177d88;padding-bottom:8px}.brand{float:left;direction:ltr;font-weight:bold;color:#177d88}h1{font-size:29pt;line-height:1.22;margin:22px 0 10px}h2{font-size:16.5pt;margin:20px 0 9px;color:#145e69}h3{font-size:12pt;margin:12px 0 5px}p{margin:8px 0}.lead{font-size:13pt;line-height:1.55}.notice{border-right:4px solid #b5473b;background:#fff3f0;padding:12px 15px;margin:15px 0}.cards{display:flex;gap:10px;margin:18px 0}.card{background:#edf4f6;flex:1;padding:12px;text-align:center;border-radius:5px}.big{font-size:22pt;font-weight:bold;direction:ltr;display:block;line-height:1.3}.small{font-size:9.5pt;color:#526774}.bad{color:#a22d2d;font-weight:bold}.good{color:#186b56}table{width:100%;border-collapse:collapse;margin:12px 0;font-size:10pt;line-height:1.4}th{background:#eaf2f4;text-align:right;padding:9px 7px}td{padding:8px 7px;border-bottom:1px solid #d7e2e5;vertical-align:top}td:not(:first-child){font-variant-numeric:tabular-nums}ul,ol{padding-right:20px;margin:8px 0}li{padding-bottom:7px}.ltr{direction:ltr;unicode-bidi:embed;display:inline-block}.hash{direction:ltr;unicode-bidi:embed;display:block;text-align:left;font-family:monospace;font-size:8.5pt;overflow-wrap:anywhere;background:#f1f5f7;padding:7px;margin:4px 0 10px}.formula{direction:ltr;text-align:center;font-family:monospace;background:#edf4f6;padding:12px;margin:12px 0;font-size:12pt}.footer{position:absolute;bottom:0;width:100%;border-top:1px solid #d7e2e5;padding-top:7px;font-size:8.5pt;color:#657a84}.footer span{float:left;direction:ltr}.note{font-size:10pt;color:#445b68}.status{display:inline-block;background:#fce7e1;color:#9b352a;border-radius:3px;font-weight:bold;font-size:11pt;padding:4px 10px}code{font-family:monospace;direction:ltr;unicode-bidi:embed;font-size:10pt}a{color:#166878;text-decoration:none}
'''
page1=f'''
<section>
<div class="kicker"><span class="brand">vOS · Engineering Review</span>דוח כשל · 18 בספטמבר 2026</div>
<h1>כשל ביצועים חוזר<br>בסוויטת מערכת ההפעלה</h1>
<span class="status">מצב: הכשל עדיין פתוח</span>
<p class="lead">בדיקת <span class="ltr">SPSC</span> אינה עומדת בסף של 80,000 הודעות לשנייה. הכשל משתחזר גם ב־ISO הקודם, שעבר את אותה בדיקה ביום הקודם.</p>
<div class="cards"><div class="card"><span class="big">80,000</span>סף הקבלה המקורי</div><div class="card"><span class="big bad">{base_rate:,}</span>ISO קודם, בביקורת הנוכחית</div><div class="card"><span class="big">110</span>בדיקות host שעברו</div></div>
<h2>מה נמדד בפועל</h2>
<p>ב־17.9 עבר ה־ISO הקודם ארבע ריצות מלאות: 57/57 תוכניות בכל ריצה, בקצב של 86,575–90,635 הודעות לשנייה. ריצות 18.9 מתועדות להלן. ספירת התוכניות מתייחסת ליציאה בקוד אפס.</p>
<table><thead><tr><th>גרסה ומסלול</th><th>עברו</th><th>נצרכו</th><th>זמן אורח, ms</th><th>הודעות/שנייה</th></tr></thead><tbody>{rows}</tbody></table>
<div class="notice"><b>המסקנה הנתמכת:</b> תיקון SYSINFO אינו הסבר מספיק להאטה. גם אותו ISO ישן נכשל כעת. הסיבה המלאה טרם זוהתה; תוצאות העבר אינן מבטיחות מעבר בתנאי הריצה הנוכחיים.</div>
<p class="note">תנאי האימות: QEMU TCG, אורח x86-64, מעבד וירטואלי אחד ו־3,072 MiB זיכרון. הסף, חלון הבדיקה של שנייה אחת וקיבולת הטבעת של 1,024 נשמרו. אין כאן אימות לחומרה פיזית או ל־SMP מלא.</p>
<div class="footer">vOS · דוח תקלה מבוסס לוגים וגיבובים<span>1 / 3</span></div>
</section>'''
page2=f'''
<section>
<div class="kicker"><span class="brand">vOS · Evidence & Diagnosis</span>ממצאים והסברים אפשריים</div>
<h2>חישוב תוצאת הכשל</h2>
<p>בגרסת הביניים נשלחו ונצרכו 41,465 הודעות לאורך 1,010 מילישניות של שעון האורח. החישוב בדוח תואם לחישוב הבדיקה:</p>
<div class="formula">floor(41,465 × 1,000 / 1,010) = 41,054 msg/s</div>
<p>בריצת הביקורת של ה־ISO הקודם נשלחו ונצרכו 41,556 הודעות באותו פרק זמן. פער הקצב בין שתי הריצות הוא כ־{relative:.2f}%. תוספת 10 מילישניות מסבירה כ־1% בלבד, ולא את הירידה לעומת תוצאות 17.9.</p>
<h2>מה נשלל ומה עדיין פתוח</h2>
<table><thead><tr><th>כיוון בדיקה</th><th>ממצא ומשמעות</th></tr></thead><tbody>
<tr><td>תוספת עבודה ללולאה</td><td>סקירת קוד המכונה מצאה הוראות והקצאת אוגרים שקולות בלולאת היצרן, לאחר נרמול הכתובות. קריאות הטלמטריה החדשות מתבצעות לפני המדידה.</td></tr>
<tr><td>האטה בקריאות מערכת</td><td>כבר לפני בדיקות SYSINFO נמדדה האטה. במדגם BIOS, ממוצע GETPID המאוחר עלה מ־8,317 ל־12,170 יחידות TSC מדווחות; בביקורת הישנה הנוכחית התקבלו 11,899. אלו אינם מחזורי CPU פיזיים מכוילים.</td></tr>
<tr><td>תזמון היצרן והצרכן</td><td>הצרכן מסתובב כשהטבעת ריקה. היצרן מוותר על המעבד רק כשהיא מלאה. מעבר של זמן המילוי מעבר לטיק של 10 ms יכול להפחית משמעותית את זמן הריצה היעיל של היצרן. זהו הסבר איכותי, שטרם הוכח כסיבה היחידה.</td></tr>
<tr><td>תנאי המארח ו־TCG</td><td>ה־ISO הישן נשאר זהה בבתים אך שינה ביצועים בין ימים. עומס, תזמון, מדיניות הספק והתנהגות התרגום הם משתנים לבדיקה; לא בודד גורם יחיד.</td></tr>
<tr><td>אובדן הודעות או מחסור בזיכרון</td><td>במדגמים שנבדקו מספר ההודעות שנשלחו שווה למספר שנצרך. לא נמצאה ראיה שמחסור בזיכרון מסביר את ההאטה.</td></tr>
</tbody></table>
<h2>תיקון האבטחה שנבדק לצד הכשל</h2>
<p>SYSINFO הישן כתב 40 בתים למבנים שחלקם הכילו 32 בלבד, והתנגש בפורמט Linux של musl. הטלמטריה הועברה לקריאה 483 עם גודל וגרסה מפורשים, מבנה משותף והעתקת usercopy בטוחה. קריאה 99 מחזירה ENOSYS ללא כתיבה. גם הטיפול בשגיאות musl תוקן.</p>
<p class="note">הבדיקות הייעודיות בודקות canaries, גדלים וגרסאות שגויים, כתובות פסולות, עמודים לקריאה בלבד, PROT_NONE ועמוד חסר. כישלון העתקה עשוי להשאיר קידומת שנכתבה; לא מובטחת כתיבה אטומית.</p>
<div class="footer">סקירה מתמטית, סקירת אבטחה והשוואת קוד מכונה<span>2 / 3</span></div>
</section>'''
page3=f'''
<section>
<div class="kicker"><span class="brand">vOS · Reproduction & Next Actions</span>שחזור ותנאי סגירה</div>
<h2>זהות התוצרים</h2>
<p class="small">ISO קודם — אותה תמונה שעברה ב־17.9 ונכשלה בביקורת 18.9</p><span class="hash">{old_iso}</span>
<p class="small">ISO של המקורות הסופיים שנבדקו ב־18.9</p><span class="hash">{iso}</span>
<p class="small">Commit בסיס של המקורות; התיקון הנוכחי מזוהה בנפרד באמצעות patch ו־manifest</p><span class="hash">{manifest['commit']}</span>
<p class="small">SHA-256 של patch המקורות הסופיים</p><span class="hash">{manifest['dirty_diff_sha256']}</span>
<h2>הפעולות הבאות לסגירת כשל הביצועים</h2>
<ol>
<li><b>ניסוי השוואתי חוזר:</b> להריץ את ה־ISO הישן והחדש לסירוגין, ללא עבודות build או בדיקות אחרות, ולתעד תנאי מארח ותפוצה של תוצאות.</li>
<li><b>לבודד את עלות קריאות המערכת:</b> למדוד בנפרד את המסלול לכניסה ולחזרה, סנכרון טבלאות הדפים והמתנה למתזמן, באמצעות גרסת אבחון מסומנת.</li>
<li><b>לאמת את השערת גבול הטיק:</b> לתעד מתי הטבעת מלאה או ריקה, זמני producer/consumer וכמות ה־yield. להשוות לריצה המקורית ללא מכשור.</li>
<li><b>לאשר תיקון רק עם ראיות:</b> להשאיר את סף 80,000, לדרוש 57/57 בריצות חוזרות של BIOS ו־UEFI, ולשמור גם את הכשלים. בנייה מוצלחת ובדיקות host אינן סוגרות את שער הביצועים.</li>
</ol>
<h2>חומרי הבדיקה</h2>
<p class="note">חבילת הראיות בפרויקט כוללת לוגים גולמיים, תוצאות JSON, פקודות QEMU, manifest של 6,392 קובצי מקור, patch, לוגי בנייה, 110 בדיקות host וסקירת סמלי musl וקוד מכונה. הסקירה מאשרת ש־<span class="ltr">sysinfo</span> ו־<span class="ltr">__lsysinfo</span> מקושרים כפונקציות לאותה כתובת.</p>
<span class="hash">docs/design/evidence/sysinfo-abi-2026-09-18/</span>
<p class="note">תוצאות העבר והמחקר הקודם נשמרו ב־<span class="ltr">health-performance-2026-09-17.md</span>. הדוח מתאר אימות באמולטור; בידוד מלא, חומרה פיזית, Secure Boot ובטיחות כל מסלולי POSIX עדיין אינם מאומתים.</p>
<div class="footer">מצב נכון לממצאים שנאספו ב־18.9.2026 · כשל הביצועים פתוח<span>3 / 3</span></div>
</section>'''
OUT.write_text('<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><title>vOS — דוח כשל ביצועים — 2026-09-18</title><style>'+css+'</style></head><body>'+page1+page2+page3+'</body></html>')
print(OUT)
print('Pending:',[c['label'] for c in cases if c['pending']])
