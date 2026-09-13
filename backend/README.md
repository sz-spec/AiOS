# 🚀 AI App Builder (VBuilder Clone)

מערכת AI מתקדמת ליצירת אפליקציות ואתרים מתיאורים בשפה טבעית, בהשראת VBuilder.

## 🌟 תכונות

- **יצירת קוד מתיאורים טבעיים** - תאר מה אתה רוצה לבנות והמערכת תיצור את הקוד
- **מערכת Multi-Agent** - אג'נטים מתמחים (ארכיטקט, פרונטאנד, בקאנד, QA) עובדים יחד
- **תיקון אוטומטי** - זיהוי ותיקון שגיאות בלולאה אוטומטית
- **אינטגרציית Git** - שמירה אוטומטית עם version control
- **מגוון frameworks** - תמיכה ב-React, Node.js, Python ועוד
- **Caching חכם** - מניעת קריאות API מיותרות

### 🆕 December 2024 Features

- **Command** - Dynamic edgeless flows, multi-agent handoffs
- **interrupt** - Human-in-the-loop approval workflows
- **Tools update state** - Tools that modify graph state directly
- **Semantic Memory** - Meaning-based memory search
- **LangMem SDK** - Complete long-term memory management
- **Agentic RAG** - RAG with grading, routing, and web fallback
- **GraphRAG** - Knowledge graph enhanced retrieval

### 🔥 December 2025 Advanced RAG (from forums/research)

- **REFRAG** (Meta) - 30x efficiency improvement
- **CLaRa** (Apple) - 16x-128x latent compression
- **FB-RAG** - Forward-Backward, 48% latency reduction
- **Weak-to-Strong GraphRAG** - 80% accuracy with 5% data
- **CAG** - Cache-Augmented, 166x cheaper
- **EntropyGuard** - 40% storage reduction via deduplication

## 📁 מבנה הפרויקט

```
vbuilder-backend/
├── __init__.py           # Package exports
├── builder.py            # Main orchestrator
├── requirements.txt      # Dependencies
├── src/
│   ├── config.py         # Configuration management
│   ├── errors.py         # Error handling & logging
│   ├── cache.py          # Multi-backend caching
│   ├── llm.py            # LLM wrapper with fallback
│   ├── code_generator.py # Code generation & validation
│   ├── efficiency.py     # Quantization, hybrid search
│   └── git_integration.py# Git operations
├── agents/
│   ├── multi_agent.py    # Multi-agent system (LangGraph)
│   ├── router_agent.py   # Smart routing
│   └── research_workflow.py # Research agents
├── hitl/                  # 🆕 Human-in-the-Loop
│   └── __init__.py       # Command, interrupt, approval
├── memory/               # 🆕 Long-term Memory
│   └── __init__.py       # Semantic search, LangMem
├── rag/                  # 🆕 RAG Module
│   ├── __init__.py       # Basic RAG, Agentic RAG, GraphRAG
│   └── advanced.py       # 🔥 REFRAG, CLaRa, FB-RAG, CAG (Dec 2025)
├── profiling/            # Langfuse integration
├── debugging/            # LangSmith integration
├── deployment/           # AWS Lambda deployment
├── tests/
│   └── test_all.py       # 200+ tests
├── examples/             # Usage examples
└── docs/
    └── LANGGRAPH_2025_PATTERNS.md  # Full documentation
```

## 🛠️ התקנה

```bash
# Clone the repository
git clone https://github.com/yourusername/vbuilder-backend.git
cd vbuilder-backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Set up API keys
export OPENAI_API_KEY="your-key-here"
# or
export ANTHROPIC_API_KEY="your-key-here"
```

## 🚀 שימוש מהיר

### Python API

```python
from vbuilder_ai_system import AIAppBuilder, Language, BuildMode

# Initialize builder
builder = AIAppBuilder()

# Simple HTML page
result = builder.build("Create a landing page for a coffee shop")

# React application
result = builder.build(
    "Build a task management app with drag-and-drop",
    language=Language.REACT,
    framework="react"
)

# Full-stack with multi-agent
result = builder.build(
    "Create a blog platform with user authentication and comments",
    mode=BuildMode.MULTI_AGENT,
    include_tests=True
)

# Check results
if result.success:
    print(f"✅ Generated {len(result.project.files)} files")
    for filename in result.project.files:
        print(f"  - {filename}")
else:
    print(f"❌ Errors: {result.errors}")
```

### Command Line

```bash
# Simple generation
python -m vbuilder_ai_system "Create a calculator app"

# With options
python -m vbuilder_ai_system "Build a weather dashboard" \
    --language react \
    --mode multi_agent \
    --tests \
    --output ./my-weather-app

# Verbose mode
python -m vbuilder_ai_system "Create an API" --verbose
```

## 📖 תיעוד מפורט

### Build Modes

| Mode | תיאור | שימוש |
|------|-------|-------|
| `SIMPLE` | יצירה בודדת ללא תיקונים | פרויקטים פשוטים |
| `ITERATIVE` | לולאת תיקון אוטומטית | ברירת מחדל |
| `MULTI_AGENT` | מערכת multi-agent מלאה | פרויקטים מורכבים |

### Languages

```python
from vbuilder_ai_system import Language

Language.HTML        # HTML/CSS/JS
Language.REACT       # React with TypeScript
Language.NODEJS      # Node.js/Express API
Language.PYTHON      # Python application
Language.TYPESCRIPT  # TypeScript
```

### Configuration

```python
from vbuilder_ai_system import Config, ModelConfig

config = Config(
    openai_api_key="sk-...",
    primary_model=ModelConfig(
        name="gpt-4o",
        temperature=0.7,
        max_tokens=4096
    ),
    cache=CacheConfig(
        enabled=True,
        backend="redis",
        ttl=3600
    ),
    git=GitConfig(
        enabled=True,
        auto_commit=True,
        remote_url="https://github.com/..."
    )
)

builder = AIAppBuilder(config=config)
```

### Multi-Agent System

המערכת כוללת 5 אג'נטים מתמחים:

1. **Architect** - תכנון ארכיטקטורה ומבנה
2. **Frontend** - פיתוח ממשק משתמש
3. **Backend** - פיתוח API ושרת
4. **Tester** - כתיבת בדיקות
5. **Reviewer** - סקירת קוד ואיכות

```python
from vbuilder_ai_system.agents import MultiAgentBuilder

builder = MultiAgentBuilder()

# Stream progress
for update in builder.build_stream("Create an e-commerce site"):
    print(f"Phase: {update['phase']}")
    print(f"Files: {update['files_generated']}")
```

### Git Integration

```python
from vbuilder_ai_system import GitManager

manager = GitManager()

# List projects
projects = manager.list_projects()

# Get history
history = manager.get_project_history("my-project")

# Export to GitHub
result = manager.export_to_github(
    project_name="my-project",
    repo_name="my-app",
    github_token="ghp_...",
    private=True
)
```

### Caching

```python
from vbuilder_ai_system.src.cache import Cache

# Memory cache (default)
cache = Cache(backend="memory", ttl=3600)

# File cache
cache = Cache(backend="file", file_path=".cache/ai")

# Redis cache
cache = Cache(backend="redis", redis_url="redis://localhost:6379")

# Decorator
@cache.cached(ttl=7200)
def expensive_operation(prompt):
    return llm.generate(prompt)
```

## 🧪 בדיקות

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=src --cov-report=html

# Specific test file
pytest tests/test_all.py -v
```

## 🔧 הרחבה

### הוספת אג'נט חדש

```python
from vbuilder_ai_system.agents.multi_agent import Agent, AgentRole

class CustomAgent(Agent):
    def __init__(self, llm=None):
        super().__init__(AgentRole.CUSTOM, llm)
        self.system_prompt = "Your custom prompt..."
    
    def invoke(self, state):
        # Your logic here
        response = self.llm.generate(prompt, system=self.system_prompt)
        return {"messages": [...], "custom_output": ...}
```

### הוספת Validator חדש

```python
from vbuilder_ai_system.src.code_generator import CodeValidator, ValidationResult

class TypeScriptValidator(CodeValidator):
    def validate(self, code: str) -> ValidationResult:
        errors = []
        warnings = []
        
        # Your validation logic
        
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )

# Register
ValidationEngine.VALIDATORS[Language.TYPESCRIPT] = TypeScriptValidator()
```

## 🔄 השוואה למסמך המקורי

### שיפורים שבוצעו:

| קטגוריה | מקורי | משופר |
|---------|-------|-------|
| Error Handling | בסיסי | Custom exceptions + retry logic + fallback |
| Caching | לא קיים | Multi-backend (Memory/File/Redis) |
| Git | לא קיים | אינטגרציה מלאה + GitHub export |
| Validation | לא קיים | HTML/JS/Python validators |
| Multi-Agent | דוגמה בסיסית | מערכת מלאה עם 5 אג'נטים |
| Configuration | Hardcoded | Dataclass-based + env vars |
| Testing | לא קיים | Comprehensive test suite |
| Logging | לא קיים | Structured logging + colored output |

### תיקוני קוד מהמסמך המקורי:

1. **LLMChain** - הוחלף ב-LCEL chains (מעודכן יותר)
2. **TavilySearch** - תוקן ל-TavilySearchResults
3. **PythonREPL** - הוספת error handling
4. **Memory** - הוספת MemorySaver מ-LangGraph
5. **Type hints** - הוספת TypedDict ו-Annotated

## 📝 License

MIT License - see LICENSE file

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing`)
5. Open Pull Request

## 📞 Support

- GitHub Issues: [Report bugs](https://github.com/yourusername/vbuilder-backend/issues)
- Discussions: [Ask questions](https://github.com/yourusername/vbuilder-backend/discussions)

---

Built with ❤️ using LangChain & LangGraph
