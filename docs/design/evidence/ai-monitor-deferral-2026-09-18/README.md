# ראיות דחיית AI monitor מה־timer IRQ

- תאריך: 2026-09-18
- commit קוד שנבדק: `4a85ba9c0136ffc2b654154d92c5e391981e783c`
- ISO: `dist/vos5.iso`
- SHA-256 ISO: `8876cb9b9c085aa687cd69e10e828bd1d18e32c74230f56c5ea42440d29a5bb0`
- SHA-256 kernel ELF: `639dfd9ad746bf04e1719f2261ac25ece1f0b918152bf2931f8487af3ad3c4fa`

ה־ISO נבנה מחדש אחרי יצירת ה־commit וקיבל אותו גיבוב שנמדד בשתי ריצות
האתחול. שתי הריצות בוצעו לבדן על המארח, ב־QEMU TCG, והגיעו ל־PMM, ‏VMM,
scheduler ופלט Setup Wizard ב־userland. ה־ISO נשאר זהה לפני ואחרי כל ריצה.

## סביבת האמולציה

- QEMU 10.2.2, בינארי SHA-256:
  `44a9a411a38b9664954fbb1d197ec003fd4c666075112166e5fc1f94a29f399a`
- UEFI code SHA-256:
  `33090cc07675baa5190d9f1e84bf5176b33bcbfa9bacac522961150cdb6dbb2a`
- UEFI vars template SHA-256:
  `5d2ac383371b408398accee7ec27c8c09ea5b74a0de0ceea6513388b15be5d1e`
- CPU אורח: `qemu64`; זיכרון: 1024MiB; ללא רשת.

## פקודות

```sh
make -C kernel BUILD_DIR=build/native-unified PRODUCTION=1 HEADLESS_AUDIT=0 iso
python3 scripts/native_boot_smoke.py \
  --iso dist/vos5.iso \
  --output /private/tmp/vos5-ai-monitor-bios-smp1 \
  --smp 1 --seconds 45
python3 scripts/native_boot_smoke.py \
  --iso dist/vos5.iso \
  --output /private/tmp/vos5-ai-monitor-uefi-smp4 \
  --smp 4 --seconds 60 \
  --firmware-code /opt/homebrew/Cellar/qemu/10.2.2/share/qemu/edk2-x86_64-code.fd \
  --firmware-vars /opt/homebrew/Cellar/qemu/10.2.2/share/qemu/edk2-i386-vars.fd
```

## גבול הטענה

הראיות מוכיחות boot-to-userland עבור BIOS/1-vCPU ו־UEFI/4-vCPU בלבד.
הן אינן מוכיחות שכל deadline של AI fired, שהעבודה הנדחית חסומה בזמן, שאין
שום callback על מחסנית IRQ, או שבידוד region ו־cancellation בטוחים. חסרים
BIOS/4-vCPU, ‏UEFI/1-vCPU, מכשור IRQ ופתרון חסמי AOOM-S1/S2.

הלוגים הסדרתיים הגולמיים נשמרים כקובצי `*.log.gz` דטרמיניסטיים כדי לשמר
בדיוק את פלט המסוף, כולל תווי CR ורווחים של מסך UEFI.
