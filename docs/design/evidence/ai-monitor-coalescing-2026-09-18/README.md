# AI monitor — שימור חציית תקופות ואימות אתחול

תאריך: 18.9.2026. commit הקוד שנבנה ונבדק:
`fedcfc895b1cc499dcb183fa1978be25c996075d`.

## מה תוקן

עבודה נדחית יכולה לאחד ticks. התנאי הישן `tick % interval == 0` פספס
דיווח כאשר זמן העובד התקדם למשל מ־999 ל־1001. השוואת המנות
`current_tick / interval > last_report_tick / interval` מזהה חציית גבול
תקופה, מדווחת פעם אחת גם לאחר דילוג גדול, ואינה מוסיפה deadline שעלול
לגלוש. interval אפס משבית דיווח. החוזה מניח שעון מונוטוני באותו boot epoch;
clock reset/wrap אינו נתמך. זהו תיקון telemetry, לא פתרון lifetime או OOM.

בדיקת host מחלצת את קוד המתזמן וה־monitor בפועל ומריצה אותו ב־UP וב־
`NATIVE_SMP_TEST`, עם ASan/UBSan. היא מכסה גם IRQ-off, פרסום תוך callback,
reentry, טיקים כפולים/לאחור, no-activity וגבול UINT64_MAX. שער
`native-build-check` כולל כעת גם את בדיקות deferred/context החסרות.
ביקורות אבטחה ומתמטיקה לא מצאו חוסם לתיקון הממוקד. אין בהן הוכחת שקילות
ל־SMP מקבילי אמיתי או הוכחה שכל callback חסום בזמן.

## תוצר ותוצאות

- ISO: `dist/vos5.iso`.
- SHA-256: `ab445cd6ee1c0f0aaeb14a7109b158408052cf20dc5c5747a460954e99e5d4c4`.
- בנייה לאחר יצירת ה־commit שמרה על אותו גיבוב; זו בנייה אינקרמנטלית,
  לא ניסוי שחזור מ־checkout נקי.
- QEMU 10.2.2, ‏q35/TCG, ‏qemu64, זיכרון 1024MiB, ללא רשת.

| סדר | מסלול | זמן תצפית | תוצאה |
|---|---|---:|---|
| 1 | BIOS / 1 vCPU | 60 שניות | PMM, VMM, scheduler, CPU online ופלט אשף ב־userland |
| 2 | UEFI / 1 vCPU | 60 שניות | אותם תנאי קבלה |
| 3 | BIOS / 4 vCPU | 60 שניות | אותם תנאי קבלה, כל ארבעת המעבדים online |
| 4 | UEFI / 4 vCPU | 60 שניות | אותם תנאי קבלה, כל ארבעת המעבדים online |

בכל ארבע הריצות: `passed=true`, אין marker כשל שה־classifier מזהה, וגיבוב
ה־ISO לפני/אחרי זהה. `observation_timeout` הוא סיום יזום של חלון תצפית
ה־VM לאחר 60 שניות, ולא timeout של תוכנית אורח או מעבר סוויטת הביצועים.
כל תצורה נצפתה פעם אחת; אין כאן מדידת יציבות סטטיסטית.

## שימור וייחוס

בכל תיקיית מסלול נשמרו `invocation.json` (פקודת harness ופקודת QEMU מלאה,
commit, גיבובי harness ו־firmware), ‏`host-before.json`, ‏`host-after.json`,
`result.json`, ופלטי serial/harness גולמיים ב־gzip עם mtime אפס. קובץ
`SHA256SUMS` מאפשר לבדוק את שלמות החבילה. `run-matrix.py` הוא בדיוק הסקריפט
שהריץ את התצפיות; ספריות הפלט בו ייחודיות ו־mkdir מסרב לדרוס ריצה קיימת.
הרצה מחדש דורשת ספריית פלט חדשה ושמירה על commit ותוצר מתאימים.

הקבצים `native-build-check.log.gz`, ‏`initial-build.log.gz` ו־
`committed-build.log.gz` משמרים את לוגי השער והבנייה מהעץ שנשמר ב־commit.
הפקודות היו:

```sh
make native-build-check
make -C kernel BUILD_DIR=build/native-unified PRODUCTION=1 HEADLESS_AUDIT=0 iso
python3 /private/tmp/vos_monitor_boot_matrix.py bios1
python3 /private/tmp/vos_monitor_boot_matrix.py uefi1
python3 /private/tmp/vos_monitor_boot_matrix.py bios4
python3 /private/tmp/vos_monitor_boot_matrix.py uefi4
```

## גבולות תנאי המארח

הריצות בוצעו ברצף; הסוכנים סיימו עבודה לפני תחילת המטריצה, ולא הורצו
build, בדיקות host, רינדור או QEMU נוסף מטעמנו בזמן תצפית. אין הוכחה שהמארח
כולו היה שקט: snapshot התהליכים נחסם בסביבת ההרצה, וגם קריאת sysctl לא
סיפקה זיהוי CPU. הכשלים נשמרו כמות שהם ולא הושלמו בדיעבד.

`pmset` דיווח `AC Power` לצד סוללה `discharging` (99%→98%). קריאת therm
החזירה שגיאות שאינן נחשבות למצב תרמי תקין, גם כשקוד היציאה הוא אפס.
לכן חבילה זו אינה קבלת ביצועים על מארח שקט או ראיה למצב חשמל יציב.

## מה עדיין פתוח

הושלמה מטריצת האתחול של התיקון. עדיין נדרשים מכשור אורח שיוכיח שעבודת
monitor אינה מתבצעת על מחסנית IRQ ושמועדים אכן מטופלים, ניתוח זמן ריצה
חסום, cancellation שאינו נוטש cleanup, region lifetime, ו־SMP production.
שער SPSC, בידוד מלא, חומרה פיזית ומדיניות AI OOM נשארו פתוחים. מצב
`ai-monitor-irq-deferral` נשאר `integrated-validation-open`.

דוח הסטטוס PDF v1.4 הוא צילום המצב שקדם למטריצה ול־commit זה; אין לשנות
אותו בדיעבד. מסמך זה ומסמך הסטטוס המתעדכן הם עדכון הראיות שלאחריו.

## בדיקת יומן האיחוד

הסריקה המחודשת שינתה את גיבוב הקובץ הקנוני `ai_monitor.c`, ולכן בודק
ההכרעות דחה תחילה את ה־scope digest הישן. הכשל נשמר ב־
`consolidation-before-scope-refresh.log.gz`. לפני עדכון ה־digest אומת שכל
נתיבי המקור, תוכנם וסיווגיהם נשארו זהים, ושהשינוי היחיד בהיטל התוכן הוא
הגיבוב של אותו קובץ קנוני שנבדק. `scope-update.json` מתעד את שני הגיבובים
והיקף השינוי. הכרעות המקור לא שונו. לוג הבדיקה לאחר העדכון נשמר בנפרד;
אין החלשה של הבודק כדי לעקוף שינויי מקור.
