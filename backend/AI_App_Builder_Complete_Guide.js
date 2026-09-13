const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, 
        Header, Footer, AlignmentType, PageOrientation, LevelFormat,
        HeadingLevel, BorderStyle, WidthType, ShadingType, PageNumber, PageBreak,
        ExternalHyperlink } = require('docx');
const fs = require('fs');

// Table styling
const tableBorder = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const cellBorders = { top: tableBorder, bottom: tableBorder, left: tableBorder, right: tableBorder };
const headerShading = { fill: "1a365d", type: ShadingType.CLEAR };
const altRowShading = { fill: "f7fafc", type: ShadingType.CLEAR };

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 24 } } },
    paragraphStyles: [
      { id: "Title", name: "Title", basedOn: "Normal",
        run: { size: 56, bold: true, color: "1a365d", font: "Arial" },
        paragraph: { spacing: { before: 0, after: 240 }, alignment: AlignmentType.CENTER } },
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, color: "1a365d", font: "Arial" },
        paragraph: { spacing: { before: 400, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, color: "2c5282", font: "Arial" },
        paragraph: { spacing: { before: 300, after: 150 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, color: "2b6cb0", font: "Arial" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 2 } },
      { id: "Code", name: "Code", basedOn: "Normal",
        run: { size: 20, font: "Courier New", color: "1a202c" },
        paragraph: { spacing: { before: 100, after: 100 }, shading: { fill: "edf2f7", type: ShadingType.CLEAR } } }
    ]
  },
  numbering: {
    config: [
      { reference: "bullet-list",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbered-1",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbered-2",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbered-3",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbered-4",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbered-5",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] }
    ]
  },
  sections: [{
    properties: {
      page: { margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } }
    },
    headers: {
      default: new Header({ children: [new Paragraph({ 
        alignment: AlignmentType.RIGHT,
        children: [new TextRun({ text: "AI App Builder - Complete Guide", italics: true, size: 20, color: "666666" })]
      })] })
    },
    footers: {
      default: new Footer({ children: [new Paragraph({ 
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: "Page ", size: 20 }), new TextRun({ children: [PageNumber.CURRENT], size: 20 }), new TextRun({ text: " of ", size: 20 }), new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 20 })]
      })] })
    },
    children: [
      // ============ TITLE PAGE ============
      new Paragraph({ heading: HeadingLevel.TITLE, children: [new TextRun("מדריך מקיף לבניית מערכת AI")] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
        children: [new TextRun({ text: "AI Application Builder", size: 40, color: "2c5282" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 400 },
        children: [new TextRun({ text: "בהשראת Lovable - מ-MVP ועד Production", size: 28, italics: true })] }),
      
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200 },
        children: [new TextRun({ text: "מסמך זה מכסה:", size: 24, bold: true })] }),
      new Paragraph({ numbering: { reference: "numbered-1", level: 0 }, children: [new TextRun({ text: "שלב 1: סקירה ושיפורים - Review & Improvements", size: 24 })] }),
      new Paragraph({ numbering: { reference: "numbered-1", level: 0 }, children: [new TextRun({ text: "שלב 2: הרחבה - Adding Missing Components", size: 24 })] }),
      new Paragraph({ numbering: { reference: "numbered-1", level: 0 }, children: [new TextRun({ text: "שלב 3: יישום מעשי - Practical Implementation", size: 24 })] }),
      new Paragraph({ numbering: { reference: "numbered-1", level: 0 }, children: [new TextRun({ text: "שלב 4: התאמה לפרויקט - Adaptation for ScreenBites AI", size: 24 })] }),

      new Paragraph({ children: [new PageBreak()] }),

      // ============ STAGE 1: REVIEW & IMPROVEMENTS ============
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("שלב 1: סקירה ושיפורים")] }),
      
      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("1.1 סקירת המסמך המקורי")] }),
      new Paragraph({ spacing: { after: 200 }, children: [
        new TextRun("המסמך המקורי הציג מבנה בסיסי לבניית מערכת AI ליצירת אפליקציות. להלן ניתוח החוזקות והחולשות:")
      ]}),

      // Strengths/Weaknesses Table
      new Table({
        columnWidths: [4680, 4680],
        rows: [
          new TableRow({
            tableHeader: true,
            children: [
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 4680, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "חוזקות ✓", bold: true, color: "FFFFFF", size: 24 })] })] }),
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 4680, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "חולשות ✗", bold: true, color: "FFFFFF", size: 24 })] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, width: { size: 4680, type: WidthType.DXA },
                children: [
                  new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("מבנה ברור ב-6 שלבים")] }),
                  new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("דוגמאות קוד עובדות")] }),
                  new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("שימוש ב-LangGraph")] }),
                  new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("Multi-agent architecture")] })
                ] }),
              new TableCell({ borders: cellBorders, width: { size: 4680, type: WidthType.DXA },
                children: [
                  new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("חסר error handling")] }),
                  new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("אין caching")] }),
                  new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("חסר validation")] }),
                  new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("אין Git integration")] })
                ] })
            ]
          })
        ]
      }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("1.2 תיקוני קוד קריטיים")] }),
      
      new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("בעיה 1: LLMChain מיושן")] }),
      new Paragraph({ children: [new TextRun({ text: "הקוד המקורי:", bold: true })] }),
      new Paragraph({ style: "Code", children: [new TextRun("from langchain.chains import LLMChain  # Deprecated!")] }),
      new Paragraph({ children: [new TextRun({ text: "הפתרון המעודכן:", bold: true, color: "2f855a" })] }),
      new Paragraph({ style: "Code", children: [new TextRun("from langchain_core.prompts import ChatPromptTemplate")] }),
      new Paragraph({ style: "Code", children: [new TextRun("chain = prompt | llm | StrOutputParser()  # LCEL syntax")] }),

      new Paragraph({ spacing: { before: 200 }, heading: HeadingLevel.HEADING_3, children: [new TextRun("בעיה 2: TavilySearch שגוי")] }),
      new Paragraph({ children: [new TextRun({ text: "הקוד המקורי:", bold: true })] }),
      new Paragraph({ style: "Code", children: [new TextRun("from langchain_tavily import TavilySearch  # Wrong class!")] }),
      new Paragraph({ children: [new TextRun({ text: "הפתרון:", bold: true, color: "2f855a" })] }),
      new Paragraph({ style: "Code", children: [new TextRun("from langchain_tavily import TavilySearchResults")] }),

      new Paragraph({ spacing: { before: 200 }, heading: HeadingLevel.HEADING_3, children: [new TextRun("בעיה 3: חסר Type Hints")] }),
      new Paragraph({ children: [new TextRun({ text: "הוספת TypedDict ל-State:", bold: true, color: "2f855a" })] }),
      new Paragraph({ style: "Code", children: [new TextRun("class ProjectState(TypedDict):")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    messages: Annotated[List[BaseMessage], add_messages]")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    requirements: str")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    generated_code: Optional[Dict[str, str]]")] }),

      new Paragraph({ children: [new PageBreak()] }),

      // ============ STAGE 2: EXPANSION ============
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("שלב 2: הרחבה - רכיבים חסרים")] }),

      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("2.1 מערכת Error Handling מקיפה")] }),
      new Paragraph({ children: [new TextRun("הוספנו מערכת שגיאות מותאמת אישית עם retry logic ו-fallback אוטומטי:")] }),

      new Paragraph({ style: "Code", spacing: { before: 100 }, children: [new TextRun("class AIBuilderError(Exception):")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    def __init__(self, message, code, details=None):")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        self.message = message")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        self.code = code  # MODEL_ERROR, VALIDATION_ERROR, etc.")] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("class ModelError(AIBuilderError): ...")] }),
      new Paragraph({ style: "Code", children: [new TextRun("class ValidationError(AIBuilderError): ...")] }),
      new Paragraph({ style: "Code", children: [new TextRun("class RateLimitError(AIBuilderError): ...")] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("2.2 מערכת Caching רב-שכבתית")] }),
      new Paragraph({ children: [new TextRun("תמיכה ב-3 backends שונים:")] }),

      new Table({
        columnWidths: [2340, 3510, 3510],
        rows: [
          new TableRow({
            tableHeader: true,
            children: [
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Backend", bold: true, color: "FFFFFF" })] })] }),
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 3510, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "יתרונות", bold: true, color: "FFFFFF" })] })] }),
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 3510, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "שימוש מומלץ", bold: true, color: "FFFFFF" })] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "Memory", bold: true })] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3510, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("מהיר, פשוט, ללא תלויות")] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3510, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("פיתוח ובדיקות")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "File", bold: true })] })] }),
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 3510, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("שרידות, ללא שרת נוסף")] })] }),
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 3510, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("שרת יחיד, staging")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "Redis", bold: true })] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3510, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("מהיר, distributed, TTL מובנה")] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3510, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("Production, scaling")] })] })
            ]
          })
        ]
      }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("2.3 Code Validation Engine")] }),
      new Paragraph({ children: [new TextRun("מערכת validation מודולרית לשפות שונות:")] }),

      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun({ text: "HTMLValidator", bold: true }), new TextRun(" - בדיקת תגיות, DOCTYPE, accessibility")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun({ text: "JavaScriptValidator", bold: true }), new TextRun(" - syntax check עם Node.js")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun({ text: "PythonValidator", bold: true }), new TextRun(" - compile check + best practices")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun({ text: "TypeScriptValidator", bold: true }), new TextRun(" - type checking integration")] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("2.4 Git Integration מלאה")] }),
      new Paragraph({ children: [new TextRun("אינטגרציה עמוקה עם Git כולל:")] }),

      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("אתחול אוטומטי של repositories")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("Auto-commit עם הודעות משמעותיות")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("יצירת branches לכל פרויקט")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("Export ישיר ל-GitHub")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("היסטוריית גרסאות ו-rollback")] }),

      new Paragraph({ children: [new PageBreak()] }),

      // ============ STAGE 3: PRACTICAL IMPLEMENTATION ============
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("שלב 3: יישום מעשי")] }),

      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.1 מבנה הפרויקט המלא")] }),

      new Paragraph({ style: "Code", children: [new TextRun("lovable-ai-system/")] }),
      new Paragraph({ style: "Code", children: [new TextRun("├── __init__.py           # Package exports")] }),
      new Paragraph({ style: "Code", children: [new TextRun("├── builder.py            # Main orchestrator")] }),
      new Paragraph({ style: "Code", children: [new TextRun("├── requirements.txt")] }),
      new Paragraph({ style: "Code", children: [new TextRun("├── src/")] }),
      new Paragraph({ style: "Code", children: [new TextRun("│   ├── config.py         # Configuration")] }),
      new Paragraph({ style: "Code", children: [new TextRun("│   ├── errors.py         # Error handling")] }),
      new Paragraph({ style: "Code", children: [new TextRun("│   ├── cache.py          # Caching system")] }),
      new Paragraph({ style: "Code", children: [new TextRun("│   ├── llm.py            # LLM wrapper")] }),
      new Paragraph({ style: "Code", children: [new TextRun("│   ├── code_generator.py # Generation")] }),
      new Paragraph({ style: "Code", children: [new TextRun("│   └── git_integration.py")] }),
      new Paragraph({ style: "Code", children: [new TextRun("├── agents/")] }),
      new Paragraph({ style: "Code", children: [new TextRun("│   └── multi_agent.py    # 5 agents")] }),
      new Paragraph({ style: "Code", children: [new TextRun("└── tests/")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    └── test_all.py")] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("3.2 קוד שימוש מהיר")] }),

      new Paragraph({ style: "Code", spacing: { before: 100 }, children: [new TextRun("from lovable_ai_system import AIAppBuilder, Language, BuildMode")] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("# Initialize")] }),
      new Paragraph({ style: "Code", children: [new TextRun("builder = AIAppBuilder()")] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("# Simple HTML")] }),
      new Paragraph({ style: "Code", children: [new TextRun('result = builder.build("Create a landing page")')] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("# React with tests")] }),
      new Paragraph({ style: "Code", children: [new TextRun("result = builder.build(")] }),
      new Paragraph({ style: "Code", children: [new TextRun('    "Build a task manager",')] }),
      new Paragraph({ style: "Code", children: [new TextRun("    language=Language.REACT,")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    include_tests=True")] }),
      new Paragraph({ style: "Code", children: [new TextRun(")")] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("# Full-stack with multi-agent")] }),
      new Paragraph({ style: "Code", children: [new TextRun("result = builder.build(")] }),
      new Paragraph({ style: "Code", children: [new TextRun('    "Create blog with auth",')] }),
      new Paragraph({ style: "Code", children: [new TextRun("    mode=BuildMode.MULTI_AGENT")] }),
      new Paragraph({ style: "Code", children: [new TextRun(")")] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("3.3 מערכת ה-Multi-Agent")] }),

      new Table({
        columnWidths: [1872, 3744, 3744],
        rows: [
          new TableRow({
            tableHeader: true,
            children: [
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 1872, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Agent", bold: true, color: "FFFFFF" })] })] }),
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "תפקיד", bold: true, color: "FFFFFF" })] })] }),
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Output", bold: true, color: "FFFFFF" })] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, width: { size: 1872, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "Architect", bold: true })] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("תכנון ארכיטקטורה, tech stack, מבנה קבצים")] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("JSON spec + file structure")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 1872, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "Frontend", bold: true })] })] }),
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("React components, styling, UX")] })] }),
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("src/components/*.tsx")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, width: { size: 1872, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "Backend", bold: true })] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("API endpoints, business logic, DB")] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("src/routes/*.ts, src/models/*")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 1872, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "Tester", bold: true })] })] }),
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("Unit tests, integration tests")] })] }),
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("tests/*.test.ts")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, width: { size: 1872, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "Reviewer", bold: true })] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("Code review, security, quality")] })] }),
              new TableCell({ borders: cellBorders, width: { size: 3744, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("Review report + fixes")] })] })
            ]
          })
        ]
      }),

      new Paragraph({ children: [new PageBreak()] }),

      // ============ STAGE 4: ADAPTATION FOR SCREENBITES ============
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("שלב 4: התאמה ל-ScreenBites AI")] }),

      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.1 סקירת הפרויקט")] }),
      new Paragraph({ children: [
        new TextRun({ text: "ScreenBites AI", bold: true }),
        new TextRun(" היא פלטפורמת הפקת וידאו מבוססת AI שמתרגמת תסריטים לסרטונים מלאים עם איכות הוליוודית.")
      ]}),

      new Paragraph({ spacing: { before: 200 }, children: [new TextRun({ text: "יכולות הפלטפורמה:", bold: true })] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("Video generation עם Veo 3, Sora 2 ו-providers נוספים")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("Character consistency engine")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("Video stitching מתקדם")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("Multi-provider orchestration לאופטימיזציית עלויות")] }),
      new Paragraph({ numbering: { reference: "bullet-list", level: 0 }, children: [new TextRun("Smart Batch system")] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("4.2 התאמת הארכיטקטורה")] }),
      new Paragraph({ children: [new TextRun("כיצד מערכת ה-AI App Builder יכולה לתמוך בפיתוח ScreenBites:")] }),

      new Paragraph({ spacing: { before: 200 }, heading: HeadingLevel.HEADING_3, children: [new TextRun("הרחבת ה-Agents")] }),

      new Paragraph({ style: "Code", children: [new TextRun("# הוספת agents מותאמים לווידאו")] }),
      new Paragraph({ style: "Code", children: [new TextRun("class VideoGenerationAgent(Agent):")] }),
      new Paragraph({ style: "Code", children: [new TextRun('    """Agent לניהול יצירת וידאו"""')] }),
      new Paragraph({ style: "Code", children: [new TextRun("    ")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    def invoke(self, state):")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        # Select optimal provider (Veo 3, Sora 2)")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        # Generate video segments")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        # Handle consistency")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        pass")] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("class ConsistencyAgent(Agent):")] }),
      new Paragraph({ style: "Code", children: [new TextRun('    """Agent לשמירה על עקביות דמויות"""')] }),
      new Paragraph({ style: "Code", children: [new TextRun("    pass")] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("class StitchingAgent(Agent):")] }),
      new Paragraph({ style: "Code", children: [new TextRun('    """Agent לאיחוד סגמנטים"""')] }),
      new Paragraph({ style: "Code", children: [new TextRun("    pass")] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_3, children: [new TextRun("Multi-Provider Orchestration")] }),

      new Paragraph({ style: "Code", children: [new TextRun("class VideoProviderManager:")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    providers = {")] }),
      new Paragraph({ style: "Code", children: [new TextRun('        "veo3": Veo3Provider(),')] }),
      new Paragraph({ style: "Code", children: [new TextRun('        "sora2": Sora2Provider(),')] }),
      new Paragraph({ style: "Code", children: [new TextRun('        "runway": RunwayProvider(),')] }),
      new Paragraph({ style: "Code", children: [new TextRun("    }")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    ")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    def select_optimal(self, requirements):")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        # Cost vs quality optimization")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        # Fallback logic")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        pass")] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("4.3 Workflow מותאם ל-ScreenBites")] }),

      new Paragraph({ style: "Code", children: [new TextRun("# LangGraph workflow לייצור וידאו")] }),
      new Paragraph({ style: "Code", children: [new TextRun("workflow = StateGraph(VideoProductionState)")] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("# Nodes")] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_node("script_analyzer", analyze_script)')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_node("scene_planner", plan_scenes)')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_node("character_extractor", extract_characters)')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_node("video_generator", generate_video)')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_node("consistency_check", check_consistency)')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_node("stitcher", stitch_segments)')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_node("post_processor", post_process)')] }),
      new Paragraph({ style: "Code", children: [new TextRun("")] }),
      new Paragraph({ style: "Code", children: [new TextRun("# Flow")] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_edge(START, "script_analyzer")')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_edge("script_analyzer", "scene_planner")')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_edge("scene_planner", "character_extractor")')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_edge("character_extractor", "video_generator")')] }),
      new Paragraph({ style: "Code", children: [new TextRun('workflow.add_conditional_edges("video_generator", route_after_gen)')] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("4.4 Smart Batch Integration")] }),

      new Paragraph({ style: "Code", children: [new TextRun("class SmartBatchProcessor:")] }),
      new Paragraph({ style: "Code", children: [new TextRun('    """מערכת batch חכמה לעיבוד מקבילי"""')] }),
      new Paragraph({ style: "Code", children: [new TextRun("    ")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    def __init__(self, max_concurrent=5):")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        self.queue = asyncio.Queue()")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        self.provider_manager = VideoProviderManager()")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        self.max_concurrent = max_concurrent")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    ")] }),
      new Paragraph({ style: "Code", children: [new TextRun("    async def process_scenes(self, scenes: List[Scene]):")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        # Group by optimal provider")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        # Process in parallel")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        # Handle failures with retry")] }),
      new Paragraph({ style: "Code", children: [new TextRun("        pass")] }),

      new Paragraph({ children: [new PageBreak()] }),

      // ============ SUMMARY ============
      new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("סיכום")] }),

      new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("מה הושג")] }),

      new Table({
        columnWidths: [2340, 7020],
        rows: [
          new TableRow({
            tableHeader: true,
            children: [
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "שלב", bold: true, color: "FFFFFF" })] })] }),
              new TableCell({ borders: cellBorders, shading: headerShading, width: { size: 7020, type: WidthType.DXA },
                children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "תוצרים", bold: true, color: "FFFFFF" })] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "1. סקירה", bold: true })] })] }),
              new TableCell({ borders: cellBorders, width: { size: 7020, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("זיהוי בעיות קוד, תיקון LLMChain, Tavily, Type hints")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "2. הרחבה", bold: true })] })] }),
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 7020, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("Error handling, Caching (3 backends), Validation, Git integration")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "3. יישום", bold: true })] })] }),
              new TableCell({ borders: cellBorders, width: { size: 7020, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("11 קבצי Python, 5 agents, CLI, API, tests")] })] })
            ]
          }),
          new TableRow({
            children: [
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 2340, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun({ text: "4. התאמה", bold: true })] })] }),
              new TableCell({ borders: cellBorders, shading: altRowShading, width: { size: 7020, type: WidthType.DXA },
                children: [new Paragraph({ children: [new TextRun("ארכיטקטורה ל-ScreenBites, Video agents, Multi-provider")] })] })
            ]
          })
        ]
      }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("קבצים שנוצרו")] }),

      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "src/config.py", bold: true }), new TextRun(" - ניהול קונפיגורציה עם dataclasses")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "src/errors.py", bold: true }), new TextRun(" - Custom exceptions + logging")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "src/cache.py", bold: true }), new TextRun(" - Multi-backend caching system")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "src/llm.py", bold: true }), new TextRun(" - LLM wrapper עם fallback")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "src/code_generator.py", bold: true }), new TextRun(" - Code generation + validation")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "src/git_integration.py", bold: true }), new TextRun(" - Git operations + GitHub export")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "agents/multi_agent.py", bold: true }), new TextRun(" - 5 specialized agents")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "builder.py", bold: true }), new TextRun(" - Main orchestrator + CLI")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "tests/test_all.py", bold: true }), new TextRun(" - Comprehensive test suite")] }),
      new Paragraph({ numbering: { reference: "numbered-2", level: 0 }, children: [new TextRun({ text: "README.md", bold: true }), new TextRun(" - Full documentation")] }),

      new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("צעדים הבאים")] }),

      new Paragraph({ numbering: { reference: "numbered-3", level: 0 }, children: [new TextRun("הגדרת API keys ב-environment variables")] }),
      new Paragraph({ numbering: { reference: "numbered-3", level: 0 }, children: [new TextRun("התקנת dependencies: pip install -r requirements.txt")] }),
      new Paragraph({ numbering: { reference: "numbered-3", level: 0 }, children: [new TextRun("הרצת בדיקות: pytest tests/ -v")] }),
      new Paragraph({ numbering: { reference: "numbered-3", level: 0 }, children: [new TextRun("שימוש ראשון: python -m lovable_ai_system \"Create a landing page\"")] }),
      new Paragraph({ numbering: { reference: "numbered-3", level: 0 }, children: [new TextRun("הרחבה עם Video agents עבור ScreenBites")] }),

      new Paragraph({ spacing: { before: 400 }, alignment: AlignmentType.CENTER, children: [
        new TextRun({ text: "Built with ", italics: true, color: "666666" }),
        new TextRun({ text: "LangChain", bold: true, color: "1a365d" }),
        new TextRun({ text: " & ", italics: true, color: "666666" }),
        new TextRun({ text: "LangGraph", bold: true, color: "1a365d" })
      ]})
    ]
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("/mnt/user-data/outputs/AI_App_Builder_Complete_Guide.docx", buffer);
  console.log("Document created successfully!");
});
