# OmniStock Enterprise - מסמך חזרה לעבודה

**תאריך עדכון אחרון:** 19 בפברואר 2026
**סטטוס:** Frontend מוכן, Backend (Convex) צריך הגדרה

---

## 📍 מיקום הפרויקט

```
/Users/snirzano/Desktop/vos7220206/VOS3/backend/projects/OmniStock_Enterprise/
├── frontend/          # Next.js 16 - מוכן ועובד
└── backend/
    └── convex/        # Schema מוכן, צריך npx convex login
```

---

## 🚀 הרצה מהירה

### Frontend (מוכן!)
```bash
cd /Users/snirzano/Desktop/vos7220206/VOS3/backend/projects/OmniStock_Enterprise/frontend
npm run dev
# פתח: http://localhost:3000
```

### Backend (Convex) - צריך הגדרה חד פעמית
```bash
cd /Users/snirzano/Desktop/vos7220206/VOS3/backend/projects/OmniStock_Enterprise/backend
npx convex login          # התחברות (פותח דפדפן)
npx convex dev --once     # אתחול פרויקט
npx convex run seedSimple:seedInventory  # הזנת נתוני דמו
```

---

## ✅ מה נבנה

### Frontend Pages (8 עמודים)
| עמוד | נתיב | סטטוס |
|------|------|-------|
| Dashboard | `/` | ✅ עובד |
| Inventory | `/inventory` | ✅ עובד |
| Products | `/products` | ✅ עובד |
| Orders | `/orders` | ✅ עובד |
| Alerts | `/alerts` | ✅ עובד |
| Suppliers | `/suppliers` | ✅ עובד |
| Reports | `/reports` | ✅ עובד |
| Settings | `/settings` | ✅ עובד |

### Components
```
src/components/
├── ui/
│   ├── card.tsx
│   ├── button.tsx
│   └── badge.tsx
├── inventory/
│   ├── dashboard.tsx
│   ├── product-table.tsx
│   └── stats-card.tsx
└── layout/
    └── sidebar.tsx      # Lucide icons, dark theme
```

### Backend (Convex Schema)
```
convex/
├── schema.ts           # 12 טבלאות
├── inventory.ts        # Queries & Mutations
├── seed.ts             # Full demo data
└── seedSimple.ts       # Simple seed
```

**טבלאות:**
- organizations, users, products, warehouses
- inventory, stockMovements, contacts
- purchaseOrders, alerts, entities, records, auditLog

---

## 🎨 עיצוב

- **Theme:** Dark (slate-900, slate-800)
- **Accent:** Blue (blue-600)
- **Icons:** Lucide React
- **Font:** Geist Sans/Mono

---

## 📦 Dependencies

### Frontend
```json
{
  "next": "16.1.6",
  "react": "latest",
  "lucide-react": "latest",
  "tailwindcss": "latest"
}
```

### Backend
```json
{
  "convex": "latest"
}
```

---

## 🔧 מה נשאר לעשות

1. [ ] `npx convex login` - התחברות ל-Convex
2. [ ] `npx convex dev` - הקמת deployment
3. [ ] הרצת seed להזנת נתונים
4. [ ] חיבור Frontend ל-Convex (useQuery, useMutation)

---

## 💡 פקודות שימושיות

```bash
# בדיקת מה רץ על port 3000
lsof -i :3000

# הריגת process על port
kill -9 $(lsof -t -i:3000)

# מעבר לתיקיית הפרויקט
cd /Users/snirzano/Desktop/vos7220206/VOS3/backend/projects/OmniStock_Enterprise

# מעבר ל-VOS3 הראשי
cd /Users/snirzano/Desktop/vos7220206/VOS3
```

---

## 🔗 קשר ל-VOS3

פרויקט OmniStock Enterprise נמצא בתוך VOS3:
```
VOS3/backend/projects/OmniStock_Enterprise/
```

VOS3 עצמו כולל:
- SmartRouter (ניתוב מודלים חכם)
- Multi-Agent System (LangGraph)
- V-Core Business OS
- MCP Server

---

**להתחלה מהירה, פשוט הרץ:**
```bash
cd /Users/snirzano/Desktop/vos7220206/VOS3/backend/projects/OmniStock_Enterprise/frontend && npm run dev
```
