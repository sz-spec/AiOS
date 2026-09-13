"""
Code Generation API Routes
==========================

Generate code from natural language descriptions.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from api.deps import get_current_user, AuthenticatedUser
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)

# Import observability and router
from src.efficiency import assign_model_with_tracking
from middleware.billing_guard import billing_guard
from api.aims_dep import aims_auth_dependency  # Phase 25 (G2): flag-gated AIMS auth

router = APIRouter(
    dependencies=[Depends(billing_guard), Depends(aims_auth_dependency)],
)


class Language(str, Enum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    REACT = "react"
    NODEJS = "nodejs"
    HTML = "html"
    CSS = "css"
    SQL = "sql"


class GenerateRequest(BaseModel):
    prompt: str
    language: Language
    framework: Optional[str] = None
    context: Optional[str] = None  # Existing code context


class GeneratedFile(BaseModel):
    path: str
    content: str
    language: str


class ValidationResult(BaseModel):
    valid: bool
    errors: List[str]
    warnings: List[str]


class GenerateResponse(BaseModel):
    code: str
    language: Language
    explanation: str
    dependencies: List[str]
    validation: ValidationResult


class ProjectRequest(BaseModel):
    prompt: str
    project_type: Optional[str] = "web"  # web, api, cli, library
    language: Optional[Language] = Language.TYPESCRIPT
    features: Optional[List[str]] = []


class ProjectResponse(BaseModel):
    name: str
    description: str
    files: List[GeneratedFile]
    dependencies: dict
    setup_instructions: str


def get_code_generator():
    """Get the code generator service."""
    try:
        from ai.codegen.generator import CodeGenerator

        return CodeGenerator()
    except ImportError:
        return None


@router.post(
    "/generate",
    response_model=GenerateResponse,
    summary="Generate code from description",
    description="Generate code from a natural language description, with automatic model selection based on complexity",
    responses={503: {"description": "AI service unavailable"}},
)
async def generate_code(
    request: GenerateRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> GenerateResponse:
    """Generate code from natural language description."""
    # Calculate complexity and get optimal model
    complexity = min(10, max(1, len(request.prompt) // 50 + 4))
    selected_model, tracker = assign_model_with_tracking("coding", complexity)

    generator = get_code_generator()

    with tracker as req:
        req.tokens_in = len(request.prompt) // 4

        if generator:
            try:
                result = generator.generate(
                    prompt=request.prompt,
                    language=request.language.value,
                    context=request.context,
                )
                req.tokens_out = len(result.code) // 4

                return GenerateResponse(
                    code=result.code,
                    language=request.language,
                    explanation=result.explanation,
                    dependencies=result.dependencies,
                    validation=ValidationResult(
                        valid=result.validation.valid,
                        errors=result.validation.errors,
                        warnings=result.validation.warnings,
                    ),
                )
            except Exception as e:
                req.success = False
                logger.error("Code generation failed: %s", e)
                raise HTTPException(status_code=500, detail="Code generation failed")
        else:
            # Development fallback
            code_templates = {
                Language.PYTHON: f'# Generated Python code\n# Prompt: {request.prompt}\n\ndef main():\n    """TODO: Implement based on prompt."""\n    pass\n\nif __name__ == "__main__":\n    main()',
                Language.TYPESCRIPT: f"// Generated TypeScript code\n// Prompt: {request.prompt}\n\nexport function main(): void {{\n  // TODO: Implement based on prompt\n}}",
                Language.JAVASCRIPT: f"// Generated JavaScript code\n// Prompt: {request.prompt}\n\nfunction main() {{\n  // TODO: Implement based on prompt\n}}\n\nmodule.exports = {{ main }};",
                Language.REACT: f'// Generated React component\n// Prompt: {request.prompt}\n\nimport React from "react";\n\nexport function Component() {{\n  return (\n    <div>\n      {{/* TODO: Implement based on prompt */}}\n    </div>\n  );\n}}',
            }

            code = code_templates.get(request.language, f"// {request.prompt}")
            req.tokens_out = len(code) // 4

            return GenerateResponse(
                code=code,
                language=request.language,
                explanation="[Dev Mode] Template code generated. Connect AI provider for real generation.",
                dependencies=[],
                validation=ValidationResult(
                    valid=True, errors=[], warnings=["Dev mode - no real validation"]
                ),
            )


@router.post(
    "/generate/project",
    response_model=ProjectResponse,
    summary="Generate project scaffold",
    description="Generate an entire project structure with multiple files from a natural language description",
    responses={503: {"description": "AI service unavailable"}},
)
async def generate_project(
    request: ProjectRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> ProjectResponse:
    """Generate entire project structure from description."""
    generator = get_code_generator()

    if generator:
        try:
            result = generator.generate_project(
                prompt=request.prompt,
                project_type=request.project_type,
                language=request.language.value if request.language else "typescript",
            )
            return ProjectResponse(
                name=result.name,
                description=result.description,
                files=[GeneratedFile(**f) for f in result.files],
                dependencies=result.dependencies,
                setup_instructions=result.setup_instructions,
            )
        except Exception as e:
            logger.error("Project generation failed: %s", e)
            raise HTTPException(status_code=500, detail="Project generation failed")
    else:
        # Development fallback
        return ProjectResponse(
            name="generated-project",
            description=f"Project based on: {request.prompt}",
            files=[
                GeneratedFile(
                    path="README.md",
                    content=f"# Generated Project\n\n{request.prompt}",
                    language="markdown",
                ),
                GeneratedFile(
                    path=(
                        "package.json"
                        if request.language
                        in [Language.TYPESCRIPT, Language.JAVASCRIPT]
                        else "requirements.txt"
                    ),
                    content=(
                        '{\n  "name": "generated-project",\n  "version": "1.0.0"\n}'
                        if request.language
                        in [Language.TYPESCRIPT, Language.JAVASCRIPT]
                        else "# Dependencies"
                    ),
                    language=(
                        "json"
                        if request.language
                        in [Language.TYPESCRIPT, Language.JAVASCRIPT]
                        else "text"
                    ),
                ),
            ],
            dependencies={},
            setup_instructions="[Dev Mode] Connect AI provider for real project generation.",
        )


@router.post(
    "/validate",
    summary="Validate code",
    description="Run validation checks on generated code for syntax errors and best-practice warnings",
    response_model=ValidationResult,
)
async def validate_code(
    code: str, language: Language, user: AuthenticatedUser = Depends(get_current_user)
) -> ValidationResult:
    """Validate generated code."""
    generator = get_code_generator()

    if generator:
        try:
            result = generator.validate(code, language.value)
            return ValidationResult(
                valid=result.valid,
                errors=result.errors,
                warnings=result.warnings,
            )
        except Exception as e:
            logger.error("Code validation failed: %s", e)
            raise HTTPException(status_code=500, detail="Code validation failed")
    else:
        # Basic validation fallback
        errors = []
        warnings = []

        if not code.strip():
            errors.append("Code is empty")

        if language == Language.PYTHON:
            if "import " in code and "from " not in code:
                warnings.append("Consider using 'from x import y' for specific imports")
        elif language in [Language.TYPESCRIPT, Language.JAVASCRIPT]:
            if "var " in code:
                warnings.append("Consider using 'const' or 'let' instead of 'var'")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )


@router.post(
    "/refactor",
    summary="Refactor code",
    description="Refactor existing code based on natural language instructions",
    response_model=GenerateResponse,
    responses={503: {"description": "AI service unavailable"}},
)
async def refactor_code(
    code: str,
    instructions: str,
    language: Language,
    user: AuthenticatedUser = Depends(get_current_user),
) -> GenerateResponse:
    """Refactor existing code based on instructions."""
    generator = get_code_generator()

    if generator:
        try:
            result = generator.refactor(
                code=code,
                instructions=instructions,
                language=language.value,
            )
            return GenerateResponse(
                code=result.code,
                language=language,
                explanation=result.explanation,
                dependencies=result.dependencies,
                validation=ValidationResult(
                    valid=result.validation.valid,
                    errors=result.validation.errors,
                    warnings=result.validation.warnings,
                ),
            )
        except Exception as e:
            logger.error("Code refactoring failed: %s", e)
            raise HTTPException(status_code=500, detail="Code refactoring failed")
    else:
        return GenerateResponse(
            code=f"// Refactored based on: {instructions}\n{code}",
            language=language,
            explanation="[Dev Mode] Original code returned. Connect AI provider for real refactoring.",
            dependencies=[],
            validation=ValidationResult(valid=True, errors=[], warnings=[]),
        )


@router.post(
    "/explain",
    summary="Explain code",
    description="Generate a natural language explanation of the provided code",
    responses={503: {"description": "AI service unavailable"}},
)
async def explain_code(
    code: str, language: Language, user: AuthenticatedUser = Depends(get_current_user)
) -> dict:
    """Explain code in natural language."""
    generator = get_code_generator()

    if generator:
        try:
            explanation = generator.explain(code, language.value)
            return {"explanation": explanation}
        except Exception as e:
            logger.error("Code explanation failed: %s", e)
            raise HTTPException(status_code=500, detail="Code explanation failed")
    else:
        lines = len(code.strip().split("\n"))
        return {
            "explanation": f"[Dev Mode] This is {language.value} code with {lines} lines. Connect AI provider for detailed explanation."
        }


# =============================================================================
# OS Development Pipeline (7-Stage Industrial Grade)
# =============================================================================


class OSFeatureRequest(BaseModel):
    """Request model for OS feature development."""

    spec: str = Field(
        ..., description="Natural language description of the OS feature to develop"
    )
    max_iterations: int = Field(
        default=3, ge=1, le=10, description="Max review-execution iterations"
    )


class OSReviewIssue(BaseModel):
    """Single issue found during code review."""

    type: str
    location: str
    severity: str
    description: str
    fix: Optional[str] = None


class OSReviewResult(BaseModel):
    """Result from the reviewer stage."""

    passed: bool
    issues: List[OSReviewIssue] = []
    suggestions: List[str] = []
    security_implications: List[str] = []


class OSFuzzTestCase(BaseModel):
    """Single fuzz test case."""

    name: str
    input_type: (
        str  # "boundary", "malformed", "overflow", "race", "exhaustion", "injection"
    )
    input_value: str
    expected_behavior: str  # "panic", "error_return", "graceful_reject", "undefined"
    rationale: str


class OSFuzzResult(BaseModel):
    """Result from the fuzzer stage (Stage 4.5)."""

    test_cases: List[OSFuzzTestCase] = []
    crash_scenarios: List[Dict[str, Any]] = []
    edge_cases_covered: List[str] = []
    recommendations: List[str] = []


class OSFeatureResponse(BaseModel):
    """Response from OS feature development pipeline."""

    architecture: str
    memory_map: str
    code: str
    review: OSReviewResult
    fuzz_results: OSFuzzResult  # Stage 4.5
    formal_proofs: Dict[str, Any]
    iterations: int
    pipeline_info: Dict[str, Any]


class OSPipelineInfoResponse(BaseModel):
    """Information about the OS pipeline configuration."""

    name: str
    version: str
    stages: List[Dict[str, Any]]
    warnings: List[str]


def get_os_pipeline():
    """Get the OS pipeline service."""
    try:
        from ai.os_pipeline import OSPipeline, get_pipeline_info

        return OSPipeline(), get_pipeline_info
    except ImportError:
        return None, None


@router.get(
    "/os-pipeline/info",
    response_model=OSPipelineInfoResponse,
    summary="Get OS pipeline info",
    description="Return configuration and stage details for the 7-stage VOS3 OS development pipeline",
)
async def get_os_pipeline_info(
    user: AuthenticatedUser = Depends(get_current_user),
) -> OSPipelineInfoResponse:
    """Get information about the OS development pipeline."""
    _, get_info = get_os_pipeline()

    if get_info:
        info = get_info()
        return OSPipelineInfoResponse(**info)

    # Development fallback
    return OSPipelineInfoResponse(
        name="VOS3 OS Development Pipeline",
        version="1.1.0 (dev mode)",
        stages=[
            {
                "number": 0,
                "name": "security_standards",
                "model": "claude-opus",
                "description": "Global requirements",
            },
            {
                "number": 1,
                "name": "architect",
                "model": "gpt-5.2-pro",
                "description": "Architecture planning",
            },
            {
                "number": 2,
                "name": "expander",
                "model": "gemini-3-pro",
                "description": "ABI verification",
            },
            {
                "number": 3,
                "name": "execution",
                "model": "claude-opus",
                "description": "Code implementation",
            },
            {
                "number": 4,
                "name": "reviewer",
                "model": "gpt-5.2-precision",
                "description": "Static analysis",
            },
            {
                "number": 4.5,
                "name": "fuzzer",
                "model": "claude-sonnet",
                "description": "Fuzz testing with extreme inputs",
            },
            {
                "number": 5,
                "name": "formal_verifier",
                "model": "gpt-5.2-pro",
                "description": "Formal proofs",
            },
        ],
        warnings=[
            "Dev mode - connect AI providers for real OS development",
            "Do NOT use Flash/Sonnet models for kernel code",
            "Small models (Sonnet) allowed ONLY for fuzzing and unit tests",
        ],
    )


@router.post(
    "/os-feature",
    response_model=OSFeatureResponse,
    summary="Develop OS feature",
    description="Run the full 7-stage industrial OS development pipeline: Architect, Expander, Execution, Reviewer, Fuzzer, and Formal Verifier",
    responses={503: {"description": "AI service unavailable"}},
)
async def develop_os_feature(
    request: OSFeatureRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> OSFeatureResponse:
    """
    Develop a new OS feature using the 7-stage industrial pipeline.

    This endpoint runs the complete VOS3 OS development pipeline:
    1. ARCHITECT (GPT-5.2 Thinking): Plans kernel structures
    2. EXPANDER (Gemini 3 Pro): Verifies ABI alignment
    3. EXECUTION (Claude Opus): Writes MISRA C code
    4. REVIEWER (GPT-5.2 Precision): Static analysis
    4.5 FUZZER (Claude Sonnet): Generates extreme inputs to stress-test
    5. FORMAL_VERIFIER (GPT-5.2 Thinking): TLA+/Coq proofs

    If review fails, the pipeline loops back to execution (up to max_iterations).
    """
    pipeline, get_info = get_os_pipeline()

    if pipeline:
        try:
            # Override max iterations if specified
            pipeline.max_review_iterations = request.max_iterations

            # Run the full pipeline
            result = await pipeline.develop_feature(request.spec)

            # Convert review issues to response format
            review_issues = [
                OSReviewIssue(
                    type=issue.get("type", "unknown"),
                    location=issue.get("location", "unknown"),
                    severity=issue.get("severity", "medium"),
                    description=issue.get("description", "No description"),
                    fix=issue.get("fix"),
                )
                for issue in result.review.issues
            ]

            # Convert fuzz test cases to response format
            fuzz_test_cases = [
                OSFuzzTestCase(
                    name=tc.name,
                    input_type=tc.input_type,
                    input_value=tc.input_value,
                    expected_behavior=tc.expected_behavior,
                    rationale=tc.rationale,
                )
                for tc in result.fuzz_results.test_cases
            ]

            return OSFeatureResponse(
                architecture=result.architecture,
                memory_map=result.memory_map,
                code=result.code,
                review=OSReviewResult(
                    passed=result.review.passed,
                    issues=review_issues,
                    suggestions=result.review.suggestions,
                    security_implications=result.review.security_implications,
                ),
                fuzz_results=OSFuzzResult(
                    test_cases=fuzz_test_cases,
                    crash_scenarios=result.fuzz_results.crash_scenarios,
                    edge_cases_covered=result.fuzz_results.edge_cases_covered,
                    recommendations=result.fuzz_results.recommendations,
                ),
                formal_proofs=result.formal_proofs,
                iterations=result.iterations,
                pipeline_info=get_info() if get_info else {},
            )
        except Exception as e:
            logger.error("OS feature development pipeline failed: %s", e)
            raise HTTPException(status_code=500, detail="OS feature development failed")

    # Development fallback
    return OSFeatureResponse(
        architecture=f"[Dev Mode] Architecture for: {request.spec}\n\n"
        "Higher-half kernel at 0xFFFF800000000000\n"
        "User space: 0x0000000000000000 - 0x00007FFFFFFFFFFF\n"
        "Kernel space: 0xFFFF800000000000 - 0xFFFFFFFFFFFFFFFF",
        memory_map="[Dev Mode] Memory map placeholder\n"
        "No ABI conflicts detected in dev mode.",
        code="[Dev Mode] Code placeholder\n\n"
        "// pmm.c - Physical Memory Manager\n"
        "// MISRA C:2024 compliant\n\n"
        "#include <stdint.h>\n"
        "#include <stdatomic.h>\n\n"
        "void* pmm_alloc(void) {\n"
        "    // TODO: Implement with real LLM\n"
        "    return NULL;\n"
        "}\n\n"
        "void pmm_free(void* ptr) {\n"
        "    // TODO: Implement with real LLM\n"
        "}",
        review=OSReviewResult(
            passed=True,
            issues=[],
            suggestions=["Connect AI providers for real code review"],
            security_implications=[],
        ),
        fuzz_results=OSFuzzResult(
            test_cases=[
                OSFuzzTestCase(
                    name="null_pointer_test",
                    input_type="malformed",
                    input_value="NULL",
                    expected_behavior="graceful_reject",
                    rationale="[Dev Mode] Example fuzz test - NULL pointer input",
                ),
                OSFuzzTestCase(
                    name="max_size_overflow",
                    input_type="overflow",
                    input_value="0xFFFFFFFFFFFFFFFF",
                    expected_behavior="error_return",
                    rationale="[Dev Mode] Example fuzz test - Maximum size overflow",
                ),
            ],
            crash_scenarios=[
                {
                    "scenario": "[Dev Mode] Example crash scenario",
                    "likelihood": "medium",
                    "impact": "kernel_panic",
                    "code_location": "pmm_alloc()",
                }
            ],
            edge_cases_covered=["NULL pointers", "Integer overflow"],
            recommendations=["Connect AI providers for real fuzz testing"],
        ),
        formal_proofs={
            "status": "dev_mode",
            "specifications": "Connect AI providers for TLA+/Coq proofs",
        },
        iterations=1,
        pipeline_info={
            "name": "VOS3 OS Development Pipeline",
            "version": "1.1.0 (dev mode)",
            "note": "Connect AI providers for real OS development",
        },
    )


@router.post(
    "/test/generate",
    summary="Generate test suite",
    description="Automatically generate unit tests for the provided code using the specified test framework",
    responses={503: {"description": "AI service unavailable"}},
)
async def generate_tests(
    code: str,
    language: Language,
    user: AuthenticatedUser = Depends(get_current_user),
    framework: Optional[str] = None,
) -> dict:
    """Generate tests for code."""
    generator = get_code_generator()

    test_frameworks = {
        Language.PYTHON: framework or "pytest",
        Language.TYPESCRIPT: framework or "vitest",
        Language.JAVASCRIPT: framework or "jest",
    }

    if generator:
        try:
            result = generator.generate_tests(
                code=code,
                language=language.value,
                framework=test_frameworks.get(language, "generic"),
            )
            return {
                "test_code": result.code,
                "framework": test_frameworks.get(language, "generic"),
                "coverage": result.coverage,
            }
        except Exception as e:
            logger.error("Test generation failed: %s", e)
            raise HTTPException(status_code=500, detail="Test generation failed")
    else:
        # Development fallback
        fw = test_frameworks.get(language, "generic")
        if language == Language.PYTHON:
            test_code = f'# Tests using {fw}\nimport pytest\n\ndef test_placeholder():\n    """TODO: Implement tests."""\n    assert True'
        else:
            test_code = f'// Tests using {fw}\ndescribe("Generated tests", () => {{\n  it("should pass", () => {{\n    expect(true).toBe(true);\n  }});\n}});'

        return {
            "test_code": test_code,
            "framework": fw,
            "coverage": 0,
        }
