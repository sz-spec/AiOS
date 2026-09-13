#!/usr/bin/env node
// Simple MCP test client

const BASE_URL = 'http://localhost:8080/mcp';

// Parse --prompt argument
function getPrompt() {
  const args = process.argv.slice(2);
  const promptIndex = args.indexOf('--prompt');
  if (promptIndex !== -1 && args[promptIndex + 1]) {
    return args[promptIndex + 1];
  }
  return 'Write a complex distributed system architecture for a global bank';
}

const TEST_PROMPT = getPrompt();

async function testMCP() {
  console.log('🧪 Testing MCP Server with SmartRouter');
  console.log(`📝 Prompt: "${TEST_PROMPT.substring(0, 60)}..."\n`);

  // Test 1: Initialize
  console.log('1️⃣ Initializing session...');
  const initRes = await fetch(BASE_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json, text/event-stream',
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: '2024-11-05',
        capabilities: {},
        clientInfo: { name: 'test-client', version: '1.0.0' },
      },
    }),
  });

  const initText = await initRes.text();
  const sessionId = initRes.headers.get('mcp-session-id');
  console.log('   Session ID:', sessionId || 'embedded in stream');

  // Parse SSE response
  const dataMatch = initText.match(/data: ({.*})/);
  if (dataMatch) {
    const data = JSON.parse(dataMatch[1]);
    console.log('   Server:', data.result?.serverInfo?.name, data.result?.serverInfo?.version);
  }

  // Test 2: List tools (need session)
  console.log('\n2️⃣ Listing available tools...');
  const toolsRes = await fetch(BASE_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json, text/event-stream',
      ...(sessionId && { 'mcp-session-id': sessionId }),
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 2,
      method: 'tools/list',
      params: {},
    }),
  });

  const toolsText = await toolsRes.text();
  const toolsMatch = toolsText.match(/data: ({.*})/);
  if (toolsMatch) {
    const data = JSON.parse(toolsMatch[1]);
    if (data.result?.tools) {
      console.log('   Available tools:');
      data.result.tools.forEach(t => console.log(`   - ${t.name}`));
    }
  }

  // Test 3: Call v_agent_chat
  console.log('\n3️⃣ Calling v_agent_chat with SmartRouter...');
  const chatRes = await fetch(BASE_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json, text/event-stream',
      'mcp-session-id': sessionId,
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 3,
      method: 'tools/call',
      params: {
        name: 'v_agent_chat',
        arguments: {
          message: TEST_PROMPT,
          streaming: false,
        },
      },
    }),
  });

  const chatText = await chatRes.text();
  console.log('   Response:', chatText.substring(0, 500));

  console.log('\n✅ Test complete!');
}

testMCP().catch(console.error);
