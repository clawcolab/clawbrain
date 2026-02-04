/**
 * ClawBrain Startup Hook Handler
 *
 * Refreshes the ClawBrain memory system on gateway startup
 * and saves session context to brain on /new command.
 */
import { spawn } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';

// Find clawbrain skill directory - check multiple possible locations
function findSkillsDir() {
  const home = os.homedir();
  const possiblePaths = [
    path.join(home, 'clawd', 'skills', 'clawbrain'),           // ClawdBot standard
    path.join(home, '.openclaw', 'skills', 'clawbrain'),       // OpenClaw standard
    path.join(home, '.clawdbot', 'skills', 'clawbrain'),       // ClawdBot alt
  ];
  
  for (const p of possiblePaths) {
    if (fs.existsSync(path.join(p, 'scripts', 'brain_bridge.py'))) {
      return p;
    }
  }
  
  // Fallback to first option
  return possiblePaths[0];
}

const SKILLS_DIR = findSkillsDir();
const BRIDGE_SCRIPT = path.join(SKILLS_DIR, 'scripts', 'brain_bridge.py');

// Agent ID from environment variable, defaults to "default"
// Set BRAIN_AGENT_ID in your service config for per-instance storage
const AGENT_ID = process.env.BRAIN_AGENT_ID || 'default';

/**
 * Execute a brain bridge command
 * @param {string} command - The command to execute
 * @param {object} args - Command arguments
 * @returns {Promise<object>} - Command result
 */
async function runBrainCommand(command, args = {}) {
  return new Promise((resolve, reject) => {
    const input = JSON.stringify({ command, args, config: {} });
    const proc = spawn('python3', [BRIDGE_SCRIPT], {
      cwd: SKILLS_DIR,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => (stdout += data.toString()));
    proc.stderr.on('data', (data) => (stderr += data.toString()));

    proc.on('close', (code) => {
      if (code !== 0) {
        console.error('[clawbrain-hook] Bridge error:', stderr);
        reject(new Error('Bridge exited with code ' + code));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (e) {
        reject(new Error('Invalid JSON: ' + stdout));
      }
    });

    proc.stdin.write(input);
    proc.stdin.end();
  });
}

/**
 * Handle gateway startup - refresh brain memory
 * @param {object} event - Gateway startup event
 */
async function handleGatewayStartup(event) {
  console.log(`[clawbrain-hook] Gateway startup detected, refreshing brain for agent "${AGENT_ID}"...`);
  try {
    const result = await runBrainCommand('refresh_on_startup', { agent_id: AGENT_ID });
    if (result.success) {
      console.log('[clawbrain-hook] Brain refreshed:', result.sync?.memories_count || 0, 'memories loaded');
    } else {
      console.error('[clawbrain-hook] Brain refresh failed:', result.error);
    }
  } catch (err) {
    console.error('[clawbrain-hook] Error refreshing brain:', err.message);
  }
}

/**
 * Handle /new command - save session to brain memory
 * @param {object} event - Command event
 */
async function handleNewCommand(event) {
  console.log(`[clawbrain-hook] /new command detected, saving session for agent "${AGENT_ID}"...`);
  const context = event.context || {};
  const sessionEntry = context.previousSessionEntry || context.sessionEntry || {};
  
  try {
    const result = await runBrainCommand('save_session', {
      agent_id: AGENT_ID,
      session_summary: sessionEntry.summary || 'Session ended by user',
      session_id: sessionEntry.sessionId || null,
    });
    if (result.success) {
      console.log('[clawbrain-hook] Session saved to brain memory');
    } else {
      console.error('[clawbrain-hook] Session save failed:', result.error);
    }
  } catch (err) {
    console.error('[clawbrain-hook] Error saving session:', err.message);
  }
}

/**
 * Main hook handler
 * @param {object} event - Hook event
 */
const clawbrainHook = async (event) => {
  // Handle gateway startup
  if (event.type === 'gateway' && event.action === 'startup') {
    await handleGatewayStartup(event);
    return;
  }

  // Handle /new command
  if (event.type === 'command' && event.action === 'new') {
    await handleNewCommand(event);
    return;
  }
};

export default clawbrainHook;
