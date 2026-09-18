# SPSC committee — existing-evidence mathematical review

Reviewer: `/root/math_build_review`, 2026-09-18. Phase 1 only: source, existing disassembly and preserved logs; no new test, compilation, VM or measurement. Canonical capacity 1024, producer interval 1000 guest milliseconds and acceptance threshold 80000 messages/s remain unchanged. Historical v1.0 reports and all failed observations remain evidence, not overwritten conclusions.

## Established observations and correction

The ABI BIOS log at `/private/tmp/vos-sysinfo-final-20260918/bios/serial.log` reports 41465 pushed and consumed over 1010 guest milliseconds: floor(41465×1000/1010)=41054/s. The old-image control reported in `docs/reports/vos-performance-failure-2026-09-18.html` gives 41556/1010=41144/s. That old ISO previously passed. This excludes the SYSINFO source change as a necessary condition for the failure; it does not isolate host load, TCG state, placement, or another environmental cause.

`user/src/test_health_check.c:217–303` spins in the consumer on empty; producer calls GETTIME every attempt and yields on full. The measured denominator also includes sentinel delivery and completion observation. The 10 ms extra denominator explains approximately 1%, not a twofold throughput loss. Equal aggregate counts do not prove payload integrity or absence of a duplicated-and-lost pair: the current acceptance condition checks rate and completion, not sequences or equality.

**Correction to earlier inference:** 1024/R is a capacity-equivalent elapsed interval, not an observed fill/drain cycle. `kernel/include/vos/scheduler.h` defines a 10-tick DEFAULT_SLICE with a 100 Hz timer; `scheduler.c:425` assigns non-idle `(priority+1)*DEFAULT_SLICE/2` ticks and can multiply AI slices. Follow-up tracing found an important omission in that initial review: scheduler.c:817 unconditionally requests BSP rescheduling every tick, irrespective of remaining slice. Thus there is a nominal 10 ms reschedule opportunity in the one-CPU benchmark. This still does not prove alternating producer/consumer or an exact 102400/s ceiling; IRQ delivery, selected tasks and occupancy must be observed. The earlier use of nominal multi-tick quantum to contradict tick-driven BSP scheduling was incorrect.

## Correctness and causality bounds

The SPSC single-producer/single-consumer head/tail protocol publishes completed slots with release and observes peer progress with acquire (`user/include/spsc.h`). Capacity is a power of two; canonical occupancy remains within 0..1024 and modulo-uint64 arithmetic is appropriate while that invariant holds. Cached peer indices can conservatively delay progress; they do not justify overwriting unread entries. This is conditional reasoning, not a formal proof of the compiler, kernel or SMP execution.

SYSINFO's safe copyout precedes timed execution; the health clone uses CLONE_VM, so no private fork-COW duplication is introduced by that clone. Actual emitted consumer code inherits parent RBP and writes an inherited R14 value into the same parent-frame spill slot. Inspection found the parent stores that same value before reuse: compiler fragility exists, but no corruption mechanism was demonstrated for this ELF. Volatile completion flags are not a general C atomic synchronization proof. These pre-existing limitations must not be presented as identified causes of the new rate.

## Experiments needed; not executed

The exact committee index is now available in `questions.md`; authoritative per-label answers and E mappings follow below. The following planning estimates are provisional wall-clock budgets, not measured durations or authorization to start.

* Paired unchanged/candidate fresh-VM control: counterbalanced order, identical command/ISO identity, one VM and no parallel host work. Preserve every rate, guest elapsed time, host elapsed time and environment record. Approximately 15–25 minutes per full-suite pair, subject to actual prior run duration; ten pairs about 2.5–4.2 hours plus preparation. This estimates reproducibility and variance, not a guaranteed causal attribution.
* Bounded diagnostic scheduling trace: counters per producer/consumer for selected quantum, switches by reason, full attempts, empty transitions, occupancy and time executing. No hot-loop logging. Approximately 1–2 hours implementation/review plus a full paired run; instrumentation must be compared to the uninstrumented image because layout and observer overhead can affect TCG.
* Calibration and timing decomposition: distinguish guest tick time, raw TSC units and host monotonic duration; measure syscall count/cost distributions and productive/empty-running intervals. Approximately 1–2 hours preparation plus paired run. No conversion of TSC units to physical CPU cycles or microseconds without calibration.

A capacity or quantum step response is supportive, not sufficient causal proof: changing either can alter code/data placement and scheduling simultaneously. Diagnosis requires the predicted occupancy/switch trajectory and controls, with altered parameters confined to labelled diagnostics. Canonical acceptance remains unchanged.

A study capable of resolving a 5% difference cannot be budgeted from a single rate or range alone. For paired log-rate differences d, a planning approximation for 80% power/two-sided 5% significance is n≈((1.96+0.84)s_d/log(1.05))² independent pairs, after a pilot estimates s_d. Small samples, serial correlation and heavy tails require more careful intervals or block resampling. Ten pairs are a pilot, not a promised 5% test. Report effect interval and all individual failures; average passing throughput cannot erase canonical failed runs.


## תשובות ממופות לשאלות הוועדה

הפניות לקוד הן ביחס לשורש הפרויקט. זמני הניסויים להלן הם הערכות תכנון בלבד, כוללים הכנה וביקורת כשנדרש מכשור, ואינם התחייבות לדיוק סטטיסטי. אין להתחיל ניסוי לפני שהסתירה לגבי quantum דווחה והתקבלה החלטת המשך. בזמן מדידה כל עבודת הסוכנים תיעצר.

**[S1] תשובה:** הצרכן מבצע polling ו־PAUSE כשהטבעת ריקה; אינו מבצע yield במסלול זה. הטיק המוגדר הוא 10 ms, אך quantum אינו טיק אחד: DEFAULT_SLICE הוא 10 ticks והבחירה מחשבת `(priority+1)*10/2`, עם חריג idle ומכפילי AI. תיקון לאחר מעקב מלא: `scheduler.c:817` מבקש reschedule ב־BSP בכל טיק ללא תלות ב־slice שנותר, וה־IRQ מממש זאת. לכן בשער UP קיימת הזדמנות reschedule נומינלית כל 10 ms; השימוש הקודם ב־quantum המרובה כדי לשלול זאת היה שגוי. בחירת המשימה בפועל עדיין אינה מתועדת. **ראיה:** `user/src/test_health_check.c:217–228`; `user/include/spsc.h:166–176`; `kernel/include/vos/scheduler.h:33–49`; `kernel/src/sched/scheduler.c:425–434,856–877`. **ביטחון:** גבוה בקוד, לא ידוע בתצפית. **חסר:** E7, כ־2–4 שעות הכנה וחמש ריצות אבחון/ביקורת, בכפוף לאורך suite.

**[S2] תשובה:** yield אינו מבטיח שהצרכן יהיה הבא. הוא קורא reschedule; בחירת משימה מעדיפה interactive ואחר כך תורים לפי עדיפות, עם FIFO בתוך התור וכללי בעלות במצב SMP האבחוני. זהות שאר המשימות הזמינות בחלון אינה ידועה. **ראיה:** `kernel/src/sched/scheduler.c:224–265,307–350,1022–1030`. **ביטחון:** גבוה לגבי היעדר הבטחה, נמוך לגבי הרצף בפועל. **חסר:** E7, אותה הקצבת זמן של S1.

**[S3] תשובה:** זמן המילוי בפועל בשני הימים אינו ידוע. 1024/R אינו מדידת מילוי; R כולל תזמון, בדיקת זמן והמתנת סיום. לא הוכח חציית גבול 10 ms מתוך הלוג. עם זאת, מעקב מתוקן מצא בקשת reschedule בכל טיק ב־BSP, ולכן 10 ms הוא גבול מועמד מבוסס קוד לבחינה, לא זמן מילוי שנמדד. **ראיה:** `test_health_check.c:265–294` והסתירה ב־S1. **ביטחון:** גבוה שהנתון חסר. **חסר:** E7 עם דגימות occupancy ומעברי full/empty; כ־2–4 שעות.

**[S4] תשובה:** מספר preemptions בלתי רצוניים לעומת yield בחלון הקנוני לא נאסף. באבחון קודם דווחו 72 ניסיונות full/yield, אך זו תמונת אבחון נפרדת, לא ספירת preemptions ולא הוכחה לריצה הקנונית הנכשלת. **ראיה:** מוני scheduler ב־`scheduler.c:875–877,1026–1028`; אין הדפסתם ב־`test_health_check.c:295–305`. **ביטחון:** גבוה לגבי החוסר. **חסר:** E7, כ־2–4 שעות.

**[S5] תשובה:** אין תוצאת capacity 512/2048 בחומר שנבדק. מדרגה יכולה להתאים להשערת תזמון אך אינה הוכחה יחידה: שינוי קיבולת משנה גם footprint ותרגום/כתובות. **ראיה:** `test_health_check.c:206–210` מגדיר 1024 בלבד. **ביטחון:** גבוה. **חסר:** E5, שתי תצורות × לפחות חמש ריצות ועוד ביקורת קנונית; כ־3–5 שעות עם הכנה.

**[S6] תשובה:** קצב consumer-yield-on-empty אינו ידוע. מעבר 80K בגרסה כזו יעיד על תגובה לשינוי מדיניות האבחון; הוא לא יסגור את השער הקנוני ולא יוכיח שהסיבה היחידה היא starvation. **ראיה:** מסלול empty הנוכחי ב־`spsc.h:166–176`; מדיניות E4 ב־`questions.md`. **ביטחון:** גבוה בגבול ההסקה. **חסר:** E4 וחמש ריצות + ביקורת, כ־2–3 שעות.

**[S7] תשובה:** הקוד מציין 100 Hz; uptime מחושב כמספר ticks כפול 1000 חלקי תדר. מספר IRQ שהגיעו בחלון והיחס לשעון המארח לא נמדדו. הפרש uptime לבדו אינו trace של מסירה/עיכוב/צבירה של IRQ. **ראיה:** `scheduler.h:43`; `kernel/src/drivers/timer.c:145–150`. **ביטחון:** גבוה בהגדרה, לא ידוע במסירה בפועל. **חסר:** E7 ותזמון host סביב החלון, כ־2–4 שעות.

**[K1] תשובה:** תיקון: לא נמצא trace מוקדם של GETPID. המספרים המוקדמים 65488/94254 הם pipe roundtrip CSW, ואסור לתייגם כ־GETPID. קיימים ממוצעי GETPID מאוחרים בין ריצות, אך אינם מוכיחים הצטברות עלות בתוך אותו תהליך או סוג מצב מסוים. בדוח המאוחר: 8317→12170 יחידות TSC בין ריצות, וביקורת ISO ישן 11899; שינוי קדם לבדיקת SYSINFO. **ראיה:** `docs/reports/vos-performance-failure-2026-09-18.html`, טבלת ממצאים. **ביטחון:** בינוני לתיאור המדגם, נמוך לסיבת הצטברות. **חסר:** E8 מוקדם/מאוחר באותו run עם state counters; כ־3–5 שעות.

**[K2] תשובה:** אין פירוק מדוד של entry/dispatch/exit/root-sync/scheduling. handler GETPID עצמו קורא current ומחזיר pid; הממוצע החיצוני כולל יותר מכך. **ראיה:** `kernel/src/arch/x86_64/syscall.c:110–120`; `kernel/src/arch/x86_64/kpti.c:28–50`. **ביטחון:** גבוה לגבי קוד handler, לא ידוע לגבי חלוקת הזמן. **חסר:** E8, כ־3–5 שעות; יש למדוד overhead של המכשור.

**[K3] תשובה:** במסלול הכנת חזרה למשתמש, תחת IRQ save ונעילת AS, מסונכרן root המשתמש: העתקת 0..256 ו־511 ואיפוס 257..510. סך 258 קריאות מקור ו־512 כתיבות destination ברמת האלגוריתם. המטרה לשקף מיפויים/הסרות ולשמר גבול KPTI החלקי הקיים; שני מיפויי kernel רחבים נשארים, ללא טענת בידוד Meltdown מלא. אין מכאן מספר מחזורי CPU. **ראיה:** `kernel/src/arch/x86_64/kpti.c:28–50`; `kernel/src/arch/x86_64/kpti_roots.c`; `docs/design/evidence/health-performance-2026-09-17.md:35–38`. **ביטחון:** גבוה. עלות בפועל: E8, כ־3–5 שעות.

**[K4] תשובה:** `bench_csw_1ms.c` מבצע 100 חזרות warmup ואחריהן 10000 דגימות, אך מדפיס min/avg/max בלבד. אי אפשר לשחזר מכך median/p99 או להבחין במלואו בין tail להאטה אחידה; מספר הדגימות אינו תחליף לשמירת התפלגות. **ראיה:** טבלת GETPID בדוח הכשל אינה כוללת quantiles; health אינו אוסף דגימות GETPID. **ביטחון:** גבוה לגבי מגבלת הנתונים. **חסר:** E8, כ־3–5 שעות.

**[K5] תשובה:** usercopy של SYSINFO נמצא לפני ה־clone והחלון. push/pop פועלים בזיכרון משותף; היצרן כן קורא GETTIME בכל ניסיון ו־YIELD כשמלא. CLONE_VM אינו fork-COW פרטי. לפיכך אין תוספת copyout של SYSINFO לכל הודעה. **ראיה:** `test_health_check.c:239–294`; `spsc.h:132–190`; `kernel/src/exec/exec_syscall.c:254–258` והמסלול CLONE_VM. **ביטחון:** גבוה. אין צורך בניסוי לקביעה המבנית; השפעת מצב עקיפה עדיין פתוחה ב־E6/E8.

**[K6] תשובה:** אין תוצאת SPSC מבודד מיד לאחר boot באותה תצורה. אי אפשר להסיק אותה מבדיקה מאוחרת ב־suite. **ראיה:** סדר הבדיקה בלוג `/private/tmp/vos-sysinfo-final-20260918/bios/serial.log`. **ביטחון:** גבוה שההשוואה חסרה. **חסר:** E6 עם GETPID ומספר runnable במהלך החלון, לפחות חמש חזרות; כ־2–3 שעות כולל הכנה.

**[M1] תשובה:** התוצאה היא throughput משולב תחת מדיניות polling/yield וקריאת זמן תכופה. היא רגישה לתזמון ולכן שימושית גם לגילוי כשל responsiveness, אבל אינה latency distribution או מדידת זמן תגובה חסום. **ראיה:** נוסחת התוצאה `test_health_check.c:292–304`. **ביטחון:** גבוה. latency אמיתי מחייב E7 עם הגדרת אירוע/תגובה; כ־2–4 שעות.

**[M2] תשובה:** טרם התקבלה התפלגות בתנאי מארח שקולים ומבוקרים; מספר ריצות ל־5% אינו ידוע. יש להתחיל E2 בחמש חזרות ולתכנן גודל מדגם לפי שונות הבדלים מזווגים, לא להבטיח חמש או עשר מספיקות. נוסחת תכנון והסתייגויות מופיעות לעיל. **ראיה:** אותו ISO עבר 86575–90635 ב־17.9 ונכשל 41144 ב־18.9, לפי דוח הכשל. **ביטחון:** גבוה לגבי חוסר היכולת לתכנן ללא שונות. **זמן:** E2 ראשוני כ־1–2 שעות; מחקר 5% פתוח עד אומדן השונות.

**[M3] תשובה:** חלון בן שנייה ורזולוציית 10 ms אינם מבטיחים יציבות. 40–88 יחידות קיבולת שקולות אינן 40–88 מחזורים עצמאיים. ההודעות תלויות זו בזו דרך scheduler/queue; אין להשתמש במספר ההודעות כגודל מדגם עצמאי. גם denominator כולל drain. **ראיה:** `test_health_check.c:265–294`; `timer.c:145–150`. **ביטחון:** גבוה. **חסר:** E2 להתפלגות ו־E7 למבנה זמן; כ־1–2 וכ־2–4 שעות בהתאמה.

**[M4] תשובה:** המקור מכנה 80K “safe floor” ומזכיר 500K על חומרה, אך לא נמצאה כאן סדרת כיול או דרישת מוצר מתועדת שמבססת מספרים אלה. הסף נשמר בהוראת המשתמש; שינויו אינו מסקנה מדעית ולא בסמכות הביקורת. **ראיה:** `test_health_check.c:301–304`; `questions.md`. **ביטחון:** גבוה לגבי המקור, לא ידוע לגבי היסטוריית כיול שלא הוצגה. **החלטה:** להחלטת CTO; E1/E2 מספקים תיאור סביבת בדיקה, לא הרשאה לשנות סף.

**[M5] תשובה:** קיים reference קנוני: `user/src/bench_ai_scale.c:203–238` ממלא 64MB באמצעות xorshift64 ללא syscall בין קריאות rdtsc. זו בדיקת compute+memory, לא ALU מבודד ולא זמן מארח מכויל. נתוני אותו ISO היסטוריים 28476000/28509000 לעומת ביקורת 43715000 הם יחס 1.533–1.535 ביחידות TSC. בסיכום E1/E2 שכבר הושלם חציון הישן 42469000 והחדש 42688000. הנתונים מצביעים על האטה גם מחוץ ל־syscall inner loop, אך אינם מפרידים memory/TCG/host scheduling. **ראיה:** סדר suite בלוג ABI; GETPID הוא במפורש syscall ואינו compute control. **ביטחון:** גבוה במגבלה. **ראיה נוספת:** `existing-metrics.json`, `e12-summary.json` בתיקיית הוועדה, נתוני root שסופקו לאחר חמש חזרות לכל image. E1/E2 אלה כבר הושלמו; אין צורך ליזום אותם מחדש עבור תשובה זו. מדידת ALU מבודדת או כיול תדר עדיין לא בוצעו.

**[M6] תשובה:** קיימים גם ערכי ביניים היסטוריים 55227 ו־74227 לצד אזורי 38–43K ו־86–98K. לכן חלוקה דיכוטומית קשיחה אינה נתמכת. מאחר שהתערבויות ותנאי ריצה שונים, גם לא הוכחה התפלגות רציפה או דו־שיאית בתנאי ניסוי יחידים. **ראיה:** `docs/design/evidence/health-performance-2026-09-17.md:29` מתעד 37923 ו־74227 באותו ISO; עקבות factorial והדוחות הקודמים שומרים את 55227. **ביטחון:** גבוה שאין בסיס להוכחת bimodality, בינוני לסיכום ההיסטורי ההטרוגני. **חסר:** E2 ואחריו מדגם לפי שונות; פיילוט כ־1–2 שעות, משך הסקת צורת התפלגות אינו ידוע מראש.

**מצב סיום Phase 1:** אין ניסוי חדש ואין אישור סגירת כשל. סתירת quantum דווחה למתאם לפני מדידה. סיימתי את עבודת הקבצים; מכאן המתנה ללא עבודת host במהלך חלונות המדידה.


## תיקון מפורש לאחר מעקב IRQ מלא — לפני E3

הסקירה הראשונית שלי הסתמכה יותר מדי על שדה time_slice. זו הייתה טעות בהסקת התנהגות מסלול ה־UP, ולא הוכחה ל־scheduler נפרד למשתמש. לא נמצא `process.c` או `process_request_resched` בעץ kernel הנוכחי. native clone מוסיף את הילד לאותו scheduler באמצעות `kernel/src/exec/exec_syscall.c:501`.

הנתיב בפועל:

1. `kernel/src/arch/x86_64/interrupts.c:849–856`: IRQ0 קורא `vos3_timer_irq_handler` ומאשר PIC EOI.
2. `kernel/src/drivers/timer.c:173–184`: מגדיל מוני tick/interrupt וקורא `vos3_sched_tick` כש־scheduler פועל.
3. `kernel/src/sched/scheduler.c:790–817`: ב־BSP, לאחר טיפול sleepers/deferred, כותב **ללא תנאי של slice** `need_reschedule=1`. המשך הפונקציה אמנם מקטין slice, אבל אינו מבטל בקשה זו.
4. `interrupts.c:871–873`: בודק את הבקשה וקורא reschedule. `scheduler.c:976–984` מחזיר runnable קודם לתור, בוחר הבא ומחליף הקשר אם שונה. `scheduler.c:307–350,360–378` מגדיר interactive/priority/FIFO, ולכן אין הבטחה כללית שדווקא הצרכן ייבחר בכל tick.
5. `scheduler.c:515–518` מחליף context; כשמסגרת interrupt שבה לריצה, `kernel/src/arch/x86_64/isr_stubs.S:205,249` מכינה user root וחוזרת ב־iretq. זה אינו מסלול scheduler נפרד עבור userland.
6. YIELD: `kernel/src/arch/x86_64/syscall.c:445,155–161` → `kernel/src/sched/task.c:930–936` → `scheduler.c:1022–1030` → reschedule. ההמשך חוזר דרך מסלול syscall, כולל הכנת root ב־`syscall_entry.S:127`.

**מסקנה מתוקנת:** על CPU0 בשער החד־מעבדי יש בקשת reschedule בכל טיק timer, גם אם quantum הנומינלי טרם נגמר. אין להסיק מכך זמן IRQ מדויק במארח, החלפת משימה בכל בקשה, שני runnable בלבד או מחזורי מילוי באורך 10 ms. הטענה 1024/R = זמן מילוי נשארת בלתי תקפה. מודל תחרות סביב גבול tick אפשרי שוב מבחינת הקוד, אך עדיין דורש E7 להוכחת המנגנון בפועל. לא נערכו בדיקות, בניות או VMs במהלך הביקורת החוזרת; העבודה הסתיימה לפני E3.

עדכון תוויות המדדים לפני E3: K1/K4/M5 תוקנו על בסיס סקירת root וקובצי existing-metrics.json/e12-summary.json. לא הורצה עבודה נוספת לצורך העדכון; נתוני E1/E2 מיוחסים במפורש לסיכום שסיפק root ולא להרצה עצמאית שלי.
