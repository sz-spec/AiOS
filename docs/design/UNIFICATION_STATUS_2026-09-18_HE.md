# מצב איחוד vOS 5 והעבודה שנותרה

תאריך צילום מצב: 18.9.2026  
עץ קנוני: `vos 5/`  
בסיס Git שנבדק: `254237e6c657e9a1b6444e73829a400e7b475c1a`  
Remote בעת הצילום: `origin/main` ב־`aefdcf59...`; הבסיס המקומי היה 7 commits לפניו  
מסמך סמכות: [דרישות הליבה](CORE_REQUIREMENTS.md), המבוססות על
[התקציר הטכני ההיסטורי](vOS_Technical_Brief.pdf), SHA-256
`c4a209477383e00f660699707fa83de2ef19294bba8284da3be6d7483644207e`.

## מסקנת הסטטוס

`vos 5` הוא עץ האינטגרציה היחיד ונבחר בו מימוש בסיס לכל 43 הרכיבים. השוואת
הבתים של כל 11 המקורות הושלמה וניתנת לשחזור. שלב זה אינו מסיים את האיחוד:
עדיין צריך להכריע סמנטית בגרסאות קוד שונות, להשלים יכולות ליבה שאינן
ממומשות, ולהעביר את המערכת דרך שערי חומרה, בידוד, התקנה ושחרור.

המצב הנכון הוא **אינטגרציה ובדיקת שחרור בתהליך**. אין כרגע בסיס ראייתי
לטענה שהמערכת תומכת בכל חומרת PC, שהיא המערכת המאובטחת או היעילה ביותר,
או שקיים ISO סופי. קיימת הוכחת QEMU מוגבלת לאתחול BIOS/UEFI, למעבד אחד
וארבעה מעבדים, לכניסה לתוכנית משתמש ולמספר שערי זיכרון ואבטחה מתוחמים.

## עקרונות שאסור לאבד באיחוד

1. ליבת x86_64 עצמאית שמאתחלת מחשב ללא Linux או Windows כמערכת מארחת.
2. מצב hosted לצד Windows, macOS ו־Linux דרך פרוטוקול VBus מאומת.
3. זיכרון, תהליכים, tenants ו־AI slots מבודדים; slot 0 הוא מתאם מורשה.
4. AI מקומי שפועל גם במצב air-gap, בלי כניסה לענן או telemetry כפויים.
5. הרשאות manifest, קדימות deny, אישור אנושי ויומן חתום לפני פעולה רגישה.
6. הפעלה מותנית של הגנות חומרה ודיווח מפורש כאשר יכולת אינה זמינה.
7. בנייה דטרמיניסטית, שרשרת אספקה ניתנת לאימות וייחוס כל ISO ל־commit/tag.
8. backend, frontend, desktop, SDK, kernel וזהות/אחסון משותפים בפרויקט אחד.
9. טענות תאימות וביצועים נגזרות ממדידות בפועל ולא משמות או בדיקות host.

## 11 המקורות שנבדקו

| מקור | HEAD שנקרא | קבצים בתחום | מצב provenance |
|---|---|---:|---|
| `VOS3` | `a76787c30d44` | 5,347 | 6 קבצים מנוהלים ו־875 לא מנוהלים נכללו כבתים נוכחיים |
| `VOS3-Cyber` | `5c7bcfe67065` | 4,400 | 225 קבצים לא מנוהלים נכללו |
| `VOS-Cyber-Standard` | אין HEAD בר־פתרון | 2,088 | כל התוכן הוא working tree; אין טענת ניקיון היסטורי |
| `vos.v1` | `afb4867220d4` | 5,330 | שינוי מנוהל אחד ו־9 קבצים לא מנוהלים נכללו |
| `vos/vos4` | `308e22fe63b1` | 4,787 | 2 שינויים מנוהלים ו־224 קבצים לא מנוהלים נכללו |
| `vos/vos4-ci-triggers` | `ad4883b17fd4` | 4,522 | metadata הועבר; HEAD נקרא בלי לתקן את המקור |
| `vos/vos4-kernel-bugfix` | `44991213936b` | 4,522 | metadata הועבר; HEAD נקרא בלי לתקן את המקור |
| `vos/vos4-track-1` | `00e6a2c6f60b` | 4,535 | metadata הועבר; HEAD נקרא בלי לתקן את המקור |
| `vos/vos4-track-2` | `958c5e3c3fe9` | 4,525 | metadata הועבר; HEAD נקרא בלי לתקן את המקור |
| `vos/vos4-track-5` | `d40d20db0424` | 4,542 | metadata הועבר; HEAD נקרא בלי לתקן את המקור |
| `vos/vos4-track-7` | `e7f14c250651` | 4,526 | metadata הועבר; HEAD נקרא בלי לתקן את המקור |

אין לשנות או לנקות את עצי המקור לפני שקיבוע provenance יסתיים. בפרט,
הקבצים הלא מנוהלים ב־VOS3, ‏VOS3-Cyber ו־vos4 הם קלט השוואה, גם כאשר רובם
גרסאות vendor או תוצרים ישנים.

## תוצאת ההשוואה הנוכחית

| מצב ביומן | נתיבים | פירוש |
|---|---:|---|
| `selected-content-retained` | 4,504 | לפחות גרסה נבחרת אחת קיימת באותו נתיב |
| `content-retained-at-other-path` | 92 | התוכן נמצא במקום אחר; התאמת בתים בלבד |
| `canonical-diverged-review` | 489 | יש קובץ קנוני, אך הוא שונה מכל גרסאות המקור |
| `missing-from-canonical` | 1,153 | נתיב המקור אינו קיים בעץ הקנוני |

בסך הכול נותחו 6,238 נתיבי מקור ב־43 רכיבים. קיימים גם 1,494 קבצים
קנוניים שאין להם מקבילה באחד מעצי המקור. ב־1,542 נתיבים נשאר לפחות וריאנט
תוכן שלא נבחר; 2,719 וריאנטים ייחודיים רשומים לבדיקה.

החלוקה התפעולית של 1,642 הנתיבים החסרים או השונים היא:

| קבוצת מיון | נתיבים | טיפול נכון |
|---|---:|---|
| Limine ו־musl, השוואת גרסאות vendor | 951 | לא להעתיק קבצים ישנים; לאמת upstream, patches ו־ABI |
| תיעוד, חומר עזר ותוצרים היסטוריים | 315 | לשמר provenance או לארכב; אינם יכולת runtime |
| קוד ותצורת מוצר | 262 | החלטת accept/replace/reject מנומקת ובדיקת התנהגות |
| קלטי בדיקה | 72 | לקשר ליכולת, להסב או לדחות עם נימוק |
| שחרור ותפעול | 42 | לבנות תהליך קנוני חדש מול הנתיבים הנוכחיים |

זו חלוקת תעדוף, לא החלטה אוטומטית. היומן המלא הוא
[`file-ledger.json`](../../consolidation/reconciliation/file-ledger.json),
ורשימת הווריאנטים היא
[`variant-review.json`](../../consolidation/reconciliation/variant-review.json).

## מפת הרכיבים

| תחום | מה נבחר ונשמר | מה עדיין פתוח |
|---|---|---|
| אתחול | Multiboot2 מתוקן, Limine ארוז, BIOS/UEFI ב־QEMU | התקנה מתמשכת, Secure Boot, מסלול Limine/KASLR, preflight וחומרה פיזית |
| CPU וארכיטקטורה | נתוני CPU מקומיים, אתחול AP, כניסת syscall/IRQ ו־KPTI חלקי | production SMP מקבילי, x2APIC רחב, NMI/SWAPGS, PCID ו־KPTI מלא |
| זיכרון | roots לכל תהליך, COW/VMA, usercopy, TLB shootdown מתוחם | להסיר direct-map/kernel mappings מה־root המוגבל; revocation ומוטציה מקביליות |
| מתזמן ותהליכים | scheduler קנוני, fork/clone/exec/signals בסיסיים | עומסי משתמש מקבילים על AP, lifecycle מלא, cancellation שלא נוטש cleanup |
| IPC | queues, SHM, futex, signals ו־SPSC | wakeup/fork interactions, SHM concurrency והכרעת רגרסיית SPSC |
| תוכניות ו־libc | musl 1.2.6, 57 תוכניות/בדיקות מובנות ומעקב build משופר | ABI edge cases, 25 וריאנטים של תוכניות ומימושי syscall חסרים/חלקיים |
| אחסון וקבצים | VFS, initramfs ומספר נתיבי POSIX | כתיבה אמיתית לדיסק, התקנה, reboot persistence, mount מלא ו־path isolation |
| דרייברים | ACPI/APIC/PCI, virtio בסיסי, console/input/network | AHCI/storage מלא, כתיבה ב־virtio-blk, מכשור פיזי, Hyper-V אמיתי |
| רשת | Ethernet/IPv4 ו־virtio-net בסיסיים | IPv6, egress policy מחובר ל־socket/connect, deferred RX ובדיקות חומרה |
| אבטחה | capabilities, taint, manifest policies וראיות isolation מתוחמות | audit של כל כניסות syscall/driver, revocation, KPTI מלא ו־Secure Boot |
| הצפנה | Ed25519/SHA ו־TLS חלקי | ML-KEM מסומן במפורש כ־stub; MMR דורש בחירת format/trust ואימות |
| AI מקומי | AI Guard, slots, GGUF/וקטורים ומסלולי NPU מותנים | inference מבודד אמיתי ב־air-gap; NPU לא זמין ב־QEMU ללא BAR מורשה |
| Backend | בסיס שירותים, הרשאות, identity, keyring, P2P ומסלולי API | 157 נתיבי מוצר hosted דורשים מיון; MMR/evidence/fleet ו־data model אחיד |
| Frontend/Convex | frontend קנוני ותיקוני membership/codegen | להכריע בעלות schema בין `frontend/convex` ל־`backend/convex`; offline identity |
| Desktop/SDK | Tauri ו־SDKs קיימים | assets ואריזה production, Windows APIs/Hyper-V transport ובדיקות בכל host |
| Build ותלויות | פקודת native אחת, build dirs מבודדים, locks וכלי פרסום ISO | פקודת release אחת לכל המוצר, CI קנוני, SBOM/signing/provenance סופיים |
| CI ושחרור | בדיקות וסקריפטים מקומיים רבים | אין כרגע workflow קנוני תחת `.github`; 18 workflows ישנים הם קלט תכנון בלבד |
| רישוי | רישיונות בסיס קיימים | קובץ Sovereign מ־VOS3 וגבולות הרישוי המעורב דורשים הכרעה לפני הפצה |

### חובות קוד שאינן רק שונות בין מקורות

העץ הקנוני עצמו מסמן יכולות חסרות: ML-KEM keygen/encaps/decaps עדיין stub;
APPLOAD דוחה משום שאין יצירת task בטוחה ובבעלות; IPv6 נזרק; כתיבת
`virtio-blk` אינה ממומשת; נתיבי storage מחזירים `ENOSYS`; פיצול הרשאות
huge-page חלקי אינו ממומש; real-time signals וזמני CPU לתהליך/חוט חלקיים;
AI Guard ו־telemetry עדיין משתמשים ב־PID placeholder בחלק מהנתיבים.
אלו חובות יישום גם אם אין קובץ donor טוב יותר.

## ראיות שכבר קיימות והגבול שלהן

- בנייה נקייה אחת יצרה ISO שנבדק ב־QEMU TCG ב־BIOS וב־UEFI, עם מעבד אחד
  וארבעה מעבדים, עד PMM/VMM, מתזמן ופלט תוכנית משתמש.
- wizard ללא דיסק הושלם במכונה זמנית עם שני מעבדים. הוא אינו התקנה.
- בדיקות תקיפה ישירות חסמו ארבע גישות זיכרון בין תהליך, victim והליבה.
  ה־restricted root עדיין ממפה PML4[256] ו־PML4[511], ולכן אין בידוד מלא.
- בדיקות AP אבחוניות הראו חישוב CPL3 על מעבדים משניים. המתזמן הרגיל טרם
  הוכח תחת התקדמות בו־זמנית ועומסי משתמש משותפים.
- בניית ISO זהה בבתים הוכחה לקלט ול־builder מתועדים מסוימים. אין עדיין
  release artifact יחיד, tag חתום או שחזור על builder בלתי תלוי.
- תיקון ABI של SYSINFO committed ואומת בתחום מתוחם. הוא אינו אישור שחרור.
- קיימים קובצי ISO רבים תחת `dist/`, וכל אחד שייך לשער אחר. אין לבחור את
  `dist/vos5.iso` כ־"הגרסה הסופית" בלי manifest חתום ותוצאות קבלה תואמות.

### כשל SPSC הפתוח

הסף הקנוני נשאר 80,000 הודעות לשנייה, חלון 1,000ms וקיבולת 1,024. במדידות
18.9, ריצות battery של שני מקורות ISO היו סביב 40K ונכשלו. ריצות AC מאוחרות
עברו סביב 81K–101K, אך אין עדיין מטריצת קבלה מלאה וזהה לשני המקורות.
גרסת אבחון שבה הצרכן עושה yield על טבעת ריקה נתנה ארבע תוצאות
81,542–82,712 לעומת controls סביב 40K–43K בתנאי battery. זה מחזק את מודל
ה־busy-wait/גבול tick, אך אינו מוכיח לבדו את הסיבה ואינו שינוי בקוד הקנוני.
ניסוי `-icount` לא הגיע ל־SPSC משום ש־`test_model_load` נתקע קודם לכן.

המקור המלא הוא [דוח SPSC v1.1](../reports/vos-performance-failure-2026-09-18-v1.1.pdf)
וחבילת הראיות תחת `docs/design/evidence/sysinfo-abi-2026-09-18/`. השער פתוח.

## שלבי העבודה שנותרו

### P0 — לקבע מקור ולסגור את ההכרעות הסמנטיות

1. ליצור manifest חתום של 11 המקורות, ה־HEAD והקבצים המקומיים שנקראו.
2. לתת לכל אחד מ־262 נתיבי המוצר החלטה מפורשת: `accept`, ‏`replace`,
   `superseded` או `reject`, עם נימוק, רישיון ובדיקה.
3. להתחיל ב־106 נתיבי מערכת ההפעלה העצמאית: 81 נתיבי native ו־25 נתיבי
   user programs; לאחריהם 9 קלטי בדיקה native.
4. להכריע רישוי VOS3 Sovereign לפני שילוב קוד ממנו.
5. לעדכן את היומן כך שלא יישאר `open` סמנטי עבור רכיב שמוכרז מאוחד.

תנאי יציאה: כל וריאנט מוצר קיבל disposition; כל יכולת שנבחרה מחוברת לנתיב
הריצה ונבדקה; לכל דחייה יש נימוק. ספירת missing לבדה אינה תנאי היציאה.

### P0 — להשלים את ליבת מערכת ההפעלה

1. לסגור KPTI מלא, TLB revocation, shared-VM concurrency ו־production SMP.
2. להשלים lifecycle של fork/clone/exec/signals, cancellation ו־cleanup.
3. לסגור את אבחון SPSC בלי לשנות את הסף או להסתיר כשל.
4. לחבר מדיניות network/capabilities לכל מסלולי הכניסה ולסגור stubs קריטיים.

תנאי יציאה: BIOS/UEFI × 1/4 CPU עוברים בנייה נקייה, עומסי משתמש בו־זמניים,
בידוד עוין, revocation, crash/reap ו־57/57 בתנאי מארח קבועים ומתועדים.

### P1 — הפעלה על מחשב ממשי

1. התקנה לדיסק, persistent storage ו־reboot recovery.
2. מטריצת AHCI/NVMe/virtio, USB input, NIC, ACPI/APIC ו־UEFI/legacy BIOS.
3. Secure Boot, enrollment, TPM/attestation והגנות CPU עם fallback גלוי.
4. לפחות שתי מכונות פיזיות שונות לפני הרחבת טענות התאימות.

תנאי יציאה: ISO שניתן להתקין ולאתחל מחדש, עם לוגים וגיבובים לכל חומרה.

### P1 — AI מקומי ומצב hosted

1. inference אמיתי ב־air-gap עם quota, reclaim ובידוד בין slots/tenants.
2. חוזה VBus יחיד בין kernel, backend, desktop ו־SDK.
3. בדיקות Windows/macOS/Linux אמיתיות, כולל Windows socket/shared memory.
4. data model וזהות אחת למצב offline ולמצב ארגוני.

תנאי יציאה: מסע משתמש מלא J0–J12 פועל בלי עצי המקור ובלי שירות ענן חובה.

### P1 — בנייה ושחרור מאוחדים

1. פקודה אחת שמייצרת kernel, userland, hosted packages, installers ו־ISO.
2. CI חדש שמתאים למבנה הנוכחי; אין להעתיק את 18 ה־workflows הישנים כמות שהם.
3. locks, SBOM, סריקות, signing, provenance ו־artifact manifest מאותה ריצה.
4. להפיק release candidate יחיד מ־commit/tag יחיד ולהפריד תוצרי אבחון.

תנאי יציאה: checkout נקי ובונה בלתי תלוי משחזרים את ה־artifacts; הגיבובים,
גרסאות הכלים ותוצאות השערים מצורפים ל־tag.

### P2 — קבלה והכרזת מוצר

נדרשת מטריצת QEMU וקבלה פיזית, בדיקות אבטחה ועומס, שיקום מכשל, התקנה,
air-gap, hosted coexistence ושחזור artifacts. רק לאחריה ניתן לפרסם טענות
מדויקות לפי החומרה והתצורות שנבדקו. "כל PC בעולם" נשאר יעד מוצר עד שקיימת
מטריצת כיסוי שתומכת בטענה.

## הגדרת סיום האיחוד

האיחוד ייחשב גמור רק כאשר מתקיימים יחד:

- כל 11 המקורות קפואים וממופים; אין יכולת מקומית ללא disposition.
- אין תלות runtime או build בעץ legacy מחוץ ל־`vos 5`.
- כל רכיב משתמש בחוזה ABI/data/identity יחיד ומגובה בבדיקת התנהגות.
- ה־OS העצמאי מאתחל, מנהל CPU וזיכרון, מריץ תוכניות, מפעיל דרייברים ומבודד
  תהליכים על QEMU ועל מטריצת חומרה פיזית מפורסמת.
- מצב hosted נבדק בפועל ב־Windows, macOS ו־Linux.
- AI מקומי עובד ללא רשת; פעולות רגישות דורשות הרשאה ואישור כנדרש.
- build נקי אחד מפיק release candidate יחיד, משוחזר בבתים וממופה ל־tag.
- כל שערי הקבלה עברו והכשלים, הדילוגים והיכולות הלא זמינות נשמרו בדוח.

## שחזור צילום המצב

```sh
python3 consolidation/inventory.py
python3 consolidation/reconcile_sources.py
(cd consolidation && python3 -m unittest test_import_baseline.py test_reconcile_sources.py)
```

בצילום זה עשר בדיקות כלי האיחוד עברו. הן מאמתות סריקה, החרגות, זיהוי
שינויים וחשבונאות; הן אינן בדיקות runtime של מערכת ההפעלה.
