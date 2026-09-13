/**
 * Node.js AI Code Review Integration
 * 
 * Based on Shakudo blog and Reddit (r/ChatGPTCoding, r/nocode) - Dec 2025
 * 
 * Features:
 * - GitHub Copilot/Octokit integration
 * - Ollama for local reviews
 * - Claude API for deep analysis
 *
 * Usage:
 *   const { aiCodeReview, reviewPR } = require('./code-review');
 *   await reviewPR('owner', 'repo', 123);
 */

const { Octokit } = require('@octokit/rest');
const Anthropic = require('@anthropic-ai/sdk');

// ============================================
// Configuration
// ============================================

const config = {
  github: {
    token: process.env.GITHUB_TOKEN,
  },
  anthropic: {
    apiKey: process.env.ANTHROPIC_API_KEY,
    model: 'claude-sonnet-4-20250514',
  },
  ollama: {
    baseUrl: process.env.OLLAMA_URL || 'http://localhost:11434',
    model: 'llama3',
  },
};

// ============================================
// Clients
// ============================================

const octokit = new Octokit({ auth: config.github.token });
const anthropic = new Anthropic({ apiKey: config.anthropic.apiKey });

// ============================================
// Review Prompts
// ============================================

const REVIEW_PROMPT = `You are an expert code reviewer. Analyze this code for:

1. SECURITY: SQL injection, XSS, authentication issues, secrets exposure
2. BUGS: Logic errors, null references, race conditions, error handling
3. PERFORMANCE: N+1 queries, memory leaks, inefficient algorithms
4. BEST PRACTICES: Code style, naming conventions, documentation

Respond with ONLY a JSON array of findings:
[{
  "title": "Issue title",
  "description": "Detailed description",
  "severity": "critical|high|medium|low|info",
  "category": "security|bug|performance|style|documentation",
  "file": "filename",
  "line": line_number,
  "suggestion": "How to fix",
  "code_fix": "Fixed code if applicable"
}]

If no issues found, return: []

Code to review:
`;

// ============================================
// Review Functions
// ============================================

/**
 * Review code using Claude API (recommended for deep analysis)
 */
async function reviewWithClaude(code, context = {}) {
  try {
    const prompt = `${REVIEW_PROMPT}

Context: ${JSON.stringify(context)}

${code}`;

    const response = await anthropic.messages.create({
      model: config.anthropic.model,
      max_tokens: 4096,
      messages: [{ role: 'user', content: prompt }],
    });

    const text = response.content[0].text;
    
    // Parse JSON from response
    const jsonMatch = text.match(/\[[\s\S]*\]/);
    if (jsonMatch) {
      return {
        provider: 'claude',
        findings: JSON.parse(jsonMatch[0]),
      };
    }

    return { provider: 'claude', findings: [], raw: text };
  } catch (error) {
    console.error('Claude review error:', error);
    return { provider: 'claude', error: error.message, findings: [] };
  }
}

/**
 * Review code using Ollama (local, free, privacy-focused)
 */
async function reviewWithOllama(code, context = {}) {
  try {
    const response = await fetch(`${config.ollama.baseUrl}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: config.ollama.model,
        prompt: `${REVIEW_PROMPT}\n${code}`,
        stream: false,
        format: 'json',
      }),
    });

    if (!response.ok) {
      throw new Error(`Ollama returned ${response.status}`);
    }

    const data = await response.json();
    const findings = JSON.parse(data.response || '[]');

    return { provider: 'ollama', findings };
  } catch (error) {
    console.error('Ollama review error:', error);
    return { provider: 'ollama', error: error.message, findings: [] };
  }
}

/**
 * Static analysis using regex patterns (fast, no API needed)
 */
function staticAnalysis(code, filename) {
  const findings = [];
  const lines = code.split('\n');

  const patterns = [
    // Security
    { pattern: /eval\s*\(/i, title: 'Eval Usage', severity: 'critical', category: 'security', suggestion: 'Avoid eval() - security risk' },
    { pattern: /innerHTML\s*=/i, title: 'XSS Risk', severity: 'high', category: 'security', suggestion: 'Use textContent instead' },
    { pattern: /password\s*=\s*['"][^'"]+['"]/i, title: 'Hardcoded Password', severity: 'critical', category: 'security', suggestion: 'Use environment variables' },
    { pattern: /api[_-]?key\s*=\s*['"][^'"]+['"]/i, title: 'Hardcoded API Key', severity: 'critical', category: 'security', suggestion: 'Use environment variables' },
    
    // Bugs
    { pattern: /catch\s*\([^)]*\)\s*\{\s*\}/i, title: 'Empty Catch Block', severity: 'medium', category: 'bug', suggestion: 'Handle or log the error' },
    { pattern: /==\s*null(?!\s*\|\|)/i, title: 'Null Check Issue', severity: 'medium', category: 'bug', suggestion: 'Use === or optional chaining' },
    
    // Performance
    { pattern: /for\s*\([^)]+\)\s*\{[^}]*await/i, title: 'Await in Loop', severity: 'high', category: 'performance', suggestion: 'Use Promise.all for parallel execution' },
    
    // Style
    { pattern: /console\.(log|debug|info)\s*\(/i, title: 'Console Statement', severity: 'low', category: 'style', suggestion: 'Remove console logs in production' },
    { pattern: /var\s+\w+\s*=/i, title: 'Var Declaration', severity: 'low', category: 'style', suggestion: 'Use const or let instead' },
  ];

  lines.forEach((line, index) => {
    patterns.forEach(({ pattern, title, severity, category, suggestion }) => {
      if (pattern.test(line)) {
        findings.push({
          title,
          description: `Found at line ${index + 1}`,
          severity,
          category,
          file: filename,
          line: index + 1,
          suggestion,
        });
      }
    });
  });

  return { provider: 'static', findings };
}

// ============================================
// GitHub Integration
// ============================================

/**
 * Get PR diff from GitHub
 */
async function getPRDiff(owner, repo, prNumber) {
  const { data } = await octokit.pulls.get({
    owner,
    repo,
    pull_number: prNumber,
    mediaType: { format: 'diff' },
  });
  return data;
}

/**
 * Get changed files in PR
 */
async function getPRFiles(owner, repo, prNumber) {
  const { data } = await octokit.pulls.listFiles({
    owner,
    repo,
    pull_number: prNumber,
  });
  return data;
}

/**
 * Get file content from GitHub
 */
async function getFileContent(owner, repo, path, ref) {
  try {
    const { data } = await octokit.repos.getContent({
      owner,
      repo,
      path,
      ref,
    });
    return Buffer.from(data.content, 'base64').toString('utf-8');
  } catch (error) {
    console.error(`Failed to get ${path}:`, error.message);
    return null;
  }
}

/**
 * Post comment to PR
 */
async function postPRComment(owner, repo, prNumber, body) {
  await octokit.issues.createComment({
    owner,
    repo,
    issue_number: prNumber,
    body,
  });
}

/**
 * Create PR review with inline comments
 */
async function createPRReview(owner, repo, prNumber, commitSha, body, event, comments) {
  await octokit.pulls.createReview({
    owner,
    repo,
    pull_number: prNumber,
    commit_id: commitSha,
    body,
    event, // APPROVE, REQUEST_CHANGES, COMMENT
    comments: comments.slice(0, 50), // GitHub limit
  });
}

// ============================================
// Main Review Functions
// ============================================

/**
 * Review code with specified provider
 * 
 * @param {string} code - Code to review
 * @param {object} options - Review options
 * @returns {Promise<object>} Review result
 */
async function aiCodeReview(code, options = {}) {
  const {
    provider = 'claude',
    filename = 'unknown',
    context = {},
  } = options;

  let result;

  switch (provider) {
    case 'claude':
      result = await reviewWithClaude(code, context);
      break;
    case 'ollama':
      result = await reviewWithOllama(code, context);
      break;
    case 'static':
      result = staticAnalysis(code, filename);
      break;
    case 'all':
      // Run all providers in parallel
      const [claudeResult, staticResult] = await Promise.all([
        reviewWithClaude(code, context),
        Promise.resolve(staticAnalysis(code, filename)),
      ]);
      
      // Merge findings
      const allFindings = [
        ...claudeResult.findings,
        ...staticResult.findings,
      ];
      
      // Deduplicate
      const seen = new Set();
      const uniqueFindings = allFindings.filter(f => {
        const key = `${f.file}:${f.line}:${f.title}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      
      result = { provider: 'all', findings: uniqueFindings };
      break;
    default:
      result = await reviewWithClaude(code, context);
  }

  // Calculate score
  const score = calculateScore(result.findings);
  result.score = score;

  return result;
}

/**
 * Review a GitHub Pull Request
 * 
 * @param {string} owner - Repository owner
 * @param {string} repo - Repository name
 * @param {number} prNumber - PR number
 * @param {object} options - Review options
 */
async function reviewPR(owner, repo, prNumber, options = {}) {
  const {
    provider = 'claude',
    autoComment = true,
    includedExtensions = ['.js', '.ts', '.tsx', '.jsx', '.py', '.go'],
  } = options;

  console.log(`🔍 Starting review for ${owner}/${repo}#${prNumber}`);

  try {
    // Get PR info
    const { data: pr } = await octokit.pulls.get({
      owner,
      repo,
      pull_number: prNumber,
    });

    const headRef = pr.head.ref;
    const headSha = pr.head.sha;

    // Get changed files
    const files = await getPRFiles(owner, repo, prNumber);
    const reviewableFiles = files.filter(f => 
      includedExtensions.some(ext => f.filename.endsWith(ext))
    );

    if (reviewableFiles.length === 0) {
      console.log('No reviewable files found');
      return { status: 'skipped', reason: 'no_reviewable_files' };
    }

    // Get file contents
    const fileContents = {};
    for (const file of reviewableFiles) {
      const content = await getFileContent(owner, repo, file.filename, headRef);
      if (content) {
        fileContents[file.filename] = content;
      }
    }

    // Review each file
    const allFindings = [];
    for (const [filename, content] of Object.entries(fileContents)) {
      console.log(`📝 Reviewing ${filename}`);
      const result = await aiCodeReview(content, {
        provider,
        filename,
        context: { pr: prNumber, repo: `${owner}/${repo}` },
      });
      
      // Add filename to findings
      result.findings.forEach(f => {
        f.file = f.file || filename;
        allFindings.push(f);
      });
    }

    // Calculate overall score
    const score = calculateScore(allFindings);

    // Post review to GitHub
    if (autoComment) {
      const summary = formatReviewSummary(allFindings, score, Object.keys(fileContents).length);
      const inlineComments = formatInlineComments(allFindings);
      
      const event = score >= 80 ? 'COMMENT' : 
                    allFindings.some(f => f.severity === 'critical') ? 'REQUEST_CHANGES' : 'COMMENT';

      await createPRReview(owner, repo, prNumber, headSha, summary, event, inlineComments);
      console.log('✅ Posted review to PR');
    }

    return {
      status: 'completed',
      score,
      findings: allFindings,
      filesReviewed: Object.keys(fileContents).length,
    };

  } catch (error) {
    console.error('Review failed:', error);
    return { status: 'error', error: error.message };
  }
}

// ============================================
// Helpers
// ============================================

function calculateScore(findings) {
  if (!findings || findings.length === 0) return 100;
  
  const weights = {
    critical: 25,
    high: 15,
    medium: 8,
    low: 3,
    info: 1,
  };
  
  const deduction = findings.reduce((sum, f) => 
    sum + (weights[f.severity] || 1), 0
  );
  
  return Math.max(0, 100 - deduction);
}

function formatReviewSummary(findings, score, filesCount) {
  const icons = {
    critical: '🔴',
    high: '🟠',
    medium: '🟡',
    low: '🔵',
    info: '⚪',
  };

  const bySeverity = {};
  findings.forEach(f => {
    bySeverity[f.severity] = (bySeverity[f.severity] || 0) + 1;
  });

  let body = '## 🤖 AI Code Review\n\n';
  body += `**Score:** ${score}/100 ${score >= 80 ? '✅' : score >= 60 ? '⚠️' : '❌'}\n`;
  body += `**Files Reviewed:** ${filesCount}\n`;
  body += `**Issues Found:** ${findings.length}\n\n`;

  if (Object.keys(bySeverity).length > 0) {
    body += '### Issues by Severity\n\n';
    body += '| Severity | Count |\n|----------|-------|\n';
    ['critical', 'high', 'medium', 'low', 'info'].forEach(sev => {
      if (bySeverity[sev]) {
        body += `| ${icons[sev]} ${sev.charAt(0).toUpperCase() + sev.slice(1)} | ${bySeverity[sev]} |\n`;
      }
    });
    body += '\n';
  }

  // Top issues
  const criticalHigh = findings.filter(f => ['critical', 'high'].includes(f.severity));
  if (criticalHigh.length > 0) {
    body += '### Critical/High Priority Issues\n\n';
    criticalHigh.slice(0, 5).forEach(f => {
      body += `#### ${icons[f.severity]} ${f.title}\n`;
      body += `📁 \`${f.file}:${f.line}\`\n\n`;
      body += `${f.description}\n\n`;
      if (f.suggestion) {
        body += `**💡 Suggestion:** ${f.suggestion}\n\n`;
      }
    });
  }

  body += '---\n*Powered by AI App Builder Code Review*\n';
  return body;
}

function formatInlineComments(findings) {
  return findings
    .filter(f => ['critical', 'high'].includes(f.severity) && f.file && f.line)
    .map(f => {
      const icons = { critical: '🔴', high: '🟠' };
      let body = `**${icons[f.severity]} ${f.severity.toUpperCase()}**: ${f.title}\n\n`;
      body += `${f.description}\n`;
      if (f.suggestion) body += `\n**💡 Suggestion:** ${f.suggestion}\n`;
      if (f.code_fix) body += `\n\`\`\`suggestion\n${f.code_fix}\n\`\`\``;
      
      return { path: f.file, line: f.line, body };
    });
}

async function storeReview(review) {
  return;
}

// ============================================
// Exports
// ============================================

module.exports = {
  aiCodeReview,
  reviewPR,
  reviewWithClaude,
  reviewWithOllama,
  staticAnalysis,
  calculateScore,
};

// CLI usage
if (require.main === module) {
  const args = process.argv.slice(2);
  if (args.length >= 3) {
    const [owner, repo, prNumber] = args;
    reviewPR(owner, repo, parseInt(prNumber))
      .then(result => console.log(JSON.stringify(result, null, 2)))
      .catch(console.error);
  } else {
    console.log('Usage: node code-review.js <owner> <repo> <pr_number>');
  }
}
