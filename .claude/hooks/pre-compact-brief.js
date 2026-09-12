/**
 * PreCompact hook — extracts a session brief from the conversation .jsonl
 * BEFORE compaction destroys context. The PostCompact hook then injects
 * a pointer to this brief so Claude can reload full context.
 *
 * Zero LLM token cost — pure JS extraction from the conversation log.
 */
const fs = require('fs');
const path = require('path');
const os = require('os');

const STATE_DIR = path.join(os.tmpdir(), 'claude-hooks-state');
const PROJECT_SLUG = 'd--projects-tower';
const CLAUDE_DIR = path.join(os.homedir(), '.claude', 'projects', PROJECT_SLUG);

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(input);
    const sessionId = data.session_id
      || process.env.CLAUDE_SESSION_ID
      || process.env.SESSION_ID
      || 'default';

    // Find the session .jsonl — try exact ID, then most recent
    let jsonlPath = path.join(CLAUDE_DIR, `${sessionId}.jsonl`);
    if (!fs.existsSync(jsonlPath)) {
      // Fallback: most recently modified .jsonl
      try {
        const files = fs.readdirSync(CLAUDE_DIR)
          .filter(f => f.endsWith('.jsonl') && !f.includes('subagent'))
          .map(f => ({ name: f, mtime: fs.statSync(path.join(CLAUDE_DIR, f)).mtimeMs }))
          .sort((a, b) => b.mtime - a.mtime);
        if (files.length > 0) {
          jsonlPath = path.join(CLAUDE_DIR, files[0].name);
        }
      } catch { /* fallback failed */ }
    }

    if (!fs.existsSync(jsonlPath)) {
      process.stdout.write('{}');
      return;
    }

    // Parse the .jsonl
    const lines = fs.readFileSync(jsonlPath, 'utf8').split('\n').filter(Boolean);

    const skills = new Set();
    const filesEdited = [];
    const filesRead = new Set();
    const toolCounts = {};
    const userRequests = [];
    const assistantDecisions = [];
    const bashCommands = [];
    const outlineOps = [];
    const gitBranches = new Set();
    let lastCwd = '';

    for (const line of lines) {
      try {
        const o = JSON.parse(line);

        // Track git branches
        if (o.gitBranch) gitBranches.add(o.gitBranch);
        if (o.cwd) lastCwd = o.cwd;

        if (o.type === 'assistant' && o.message && o.message.content) {
          for (const block of o.message.content) {
            if (block.type === 'tool_use') {
              const name = block.name;
              toolCounts[name] = (toolCounts[name] || 0) + 1;

              if (name === 'Skill' && block.input) {
                skills.add(block.input.skill);
              }
              if ((name === 'Edit' || name === 'Write') && block.input && block.input.file_path) {
                const fp = block.input.file_path.replace(/.*[/\\]tower[/\\]?/, '');
                if (fp && !filesEdited.includes(fp)) filesEdited.push(fp);
              }
              if (name === 'Read' && block.input && block.input.file_path) {
                const fp = block.input.file_path.replace(/.*[/\\]tower[/\\]?/, '');
                if (fp) filesRead.add(fp);
              }
              if (name === 'Bash' && block.input && block.input.command) {
                const cmd = block.input.command.substring(0, 200);
                if (!cmd.includes('echo') && !cmd.includes('head -') && cmd.length > 10) {
                  bashCommands.push(cmd);
                }
              }
              if (name && name.startsWith('mcp__outline__')) {
                const op = name.replace('mcp__outline__', '');
                const title = block.input && (block.input.query || block.input.title || block.input.id || '');
                outlineOps.push(`${op}: ${String(title).substring(0, 80)}`);
              }
            }
            // Capture assistant text decisions (short summaries only)
            if (block.type === 'text' && block.text && block.text.length > 50 && block.text.length < 500) {
              // Only keep lines that look like decisions/summaries
              if (block.text.match(/\b(moved|created|updated|fixed|deleted|renamed|built|pushed|merged|migrated|executed|done|completed)\b/i)) {
                assistantDecisions.push(block.text.substring(0, 300));
              }
            }
          }
        }

        if (o.type === 'user' && o.message && o.message.content) {
          for (const block of o.message.content) {
            if (block.type === 'text' && block.text && !block.text.startsWith('<') && block.text.length > 15) {
              userRequests.push(block.text.substring(0, 200));
            }
          }
        }
      } catch { /* skip malformed lines */ }
    }

    // Build the brief
    const brief = [];
    brief.push(`# Session Brief — Pre-Compaction Snapshot`);
    brief.push(`Generated: ${new Date().toISOString()}`);
    brief.push(`Session: ${sessionId}`);
    brief.push(`CWD: ${lastCwd}`);
    brief.push('');

    brief.push('## User Requests (chronological)');
    userRequests.forEach((r, i) => brief.push(`${i + 1}. ${r}`));
    brief.push('');

    brief.push('## Skills Loaded');
    if (skills.size > 0) {
      [...skills].forEach(s => brief.push(`- ${s}`));
    } else {
      brief.push('- (none)');
    }
    brief.push('');

    brief.push('## Files Edited/Created');
    filesEdited.forEach(f => brief.push(`- ${f}`));
    brief.push('');

    brief.push('## Files Read');
    [...filesRead].slice(0, 30).forEach(f => brief.push(`- ${f}`));
    brief.push('');

    brief.push('## Tool Usage');
    Object.entries(toolCounts)
      .sort((a, b) => b[1] - a[1])
      .forEach(([name, count]) => brief.push(`- ${name}: ${count} calls`));
    brief.push('');

    brief.push('## Key Bash Commands');
    bashCommands.slice(-20).forEach(c => brief.push(`- ${c}`));
    brief.push('');

    brief.push('## Outline Operations');
    outlineOps.slice(-20).forEach(o => brief.push(`- ${o}`));
    brief.push('');

    brief.push('## Assistant Decisions/Actions');
    assistantDecisions.slice(-15).forEach(d => brief.push(`- ${d}`));
    brief.push('');

    brief.push('## Active Git Branches');
    [...gitBranches].forEach(b => brief.push(`- ${b}`));
    brief.push('');

    // Write the brief
    if (!fs.existsSync(STATE_DIR)) {
      fs.mkdirSync(STATE_DIR, { recursive: true });
    }
    const briefPath = path.join(STATE_DIR, `session-brief-${sessionId}.md`);
    fs.writeFileSync(briefPath, brief.join('\n'));

    // Also write the brief path for PostCompact to find
    const pointerPath = path.join(STATE_DIR, `session-brief-pointer-${sessionId}.json`);
    fs.writeFileSync(pointerPath, JSON.stringify({ briefPath, sessionId, timestamp: Date.now() }));

  } catch { /* never fail */ }

  process.stdout.write('{}');
});
