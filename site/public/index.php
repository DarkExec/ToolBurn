<?php
declare(strict_types=1);

$readmePath = dirname(__DIR__, 2) . '/README.md';
$renderer = __DIR__ . '/render_readme.py';
$repoUrl = 'https://github.com/DarkExec/ToolBurn';

function run_renderer(string $renderer, string $readmePath): array
{
    $command = ['python3', $renderer, '--readme', $readmePath];
    $descriptorSpec = [
        1 => ['pipe', 'w'],
        2 => ['pipe', 'w'],
    ];
    $process = proc_open($command, $descriptorSpec, $pipes);
    if (!is_resource($process)) {
        return [1, '', 'Could not start renderer'];
    }
    $html = stream_get_contents($pipes[1]);
    $error = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $status = proc_close($process);
    return [$status, $html ?: '', $error ?: ''];
}

[$status, $contentHtml, $renderError] = run_renderer($renderer, $readmePath);
$updatedAt = is_file($readmePath) ? date('Y-m-d H:i:s T', filemtime($readmePath)) : 'unknown';
if ($status !== 0 || trim($contentHtml) === '') {
    http_response_code(500);
    $contentHtml = '<h1>Toolburn</h1><p>The README renderer failed.</p><pre>' . htmlspecialchars($renderError, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') . '</pre>';
}
?>
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Toolburn - Local Token Burn Profiler</title>
    <meta name="description" content="Toolburn is a local-first token burn profiler for coding agents. Find which humans, background jobs, sessions, and tools used the tokens.">
    <link rel="canonical" href="https://toolburn.com/">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://toolburn.com/">
    <meta property="og:site_name" content="Toolburn">
    <meta property="og:title" content="Toolburn - Local Token Burn Profiler">
    <meta property="og:description" content="Find which humans, background jobs, sessions, and tools used the tokens.">
    <meta property="og:image" content="https://toolburn.com/assets/toolburn-logo-text.png">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="theme-color" content="#0a0b0f">
    <link rel="icon" type="image/png" href="/assets/toolburn-logo.png">
    <link rel="apple-touch-icon" href="/assets/toolburn-logo.png">
    <style>
      :root {
        color-scheme: dark;
        --bg: #090a0d;
        --panel: #11131a;
        --panel-soft: #171a22;
        --line: rgba(255, 255, 255, 0.12);
        --line-strong: rgba(255, 255, 255, 0.2);
        --text: #f4f6fb;
        --muted: #a8afbf;
        --faint: #7d8493;
        --accent: #ff6a1a;
        --accent-strong: #ff8a3d;
        --code: #10141d;
        --code-border: rgba(255, 255, 255, 0.14);
        --max: 1040px;
      }

      * { box-sizing: border-box; }

      html { scroll-behavior: smooth; }

      body {
        margin: 0;
        background:
          radial-gradient(circle at 30% -10%, rgba(255, 106, 26, 0.18), transparent 34%),
          linear-gradient(180deg, #0d0e12 0%, var(--bg) 440px);
        color: var(--text);
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        line-height: 1.65;
      }

      a { color: var(--accent-strong); text-decoration-color: rgba(255, 138, 61, 0.4); }
      a:hover { text-decoration-color: currentColor; }

      .site-header {
        border-bottom: 1px solid var(--line);
        background: rgba(9, 10, 13, 0.82);
        backdrop-filter: blur(14px);
        position: sticky;
        top: 0;
        z-index: 10;
      }

      .nav {
        width: min(var(--max), calc(100% - 32px));
        min-height: 64px;
        margin: 0 auto;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
      }

      .brand {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        color: var(--text);
        text-decoration: none;
        font-weight: 760;
      }

      .mark {
        width: 30px;
        height: 30px;
        border-radius: 8px;
        display: block;
      }

      .nav-links {
        display: flex;
        align-items: center;
        gap: 14px;
        font-size: 14px;
      }

      .nav-links a {
        color: var(--muted);
        text-decoration: none;
      }

      .nav-links .button {
        min-height: 36px;
        padding: 0 12px;
        border: 1px solid var(--line-strong);
        border-radius: 8px;
        display: inline-flex;
        align-items: center;
        color: var(--text);
        background: rgba(255, 255, 255, 0.04);
      }

      .hero {
        width: min(var(--max), calc(100% - 32px));
        margin: 0 auto;
        padding: 72px 0 34px;
      }

      .hero h1 {
        margin: 0;
        font-size: clamp(40px, 5.4vw, 58px);
        line-height: 0.96;
        letter-spacing: 0;
      }

      .hero > p:not(.meta) {
        max-width: 880px;
        margin: 24px 0 0;
        color: var(--muted);
        font-size: 20px;
      }

      .install {
        margin-top: 30px;
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 10px;
        max-width: 100%;
        align-items: stretch;
      }

      .command-box {
        min-width: 0;
        min-height: 56px;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        align-items: center;
        gap: 10px;
        padding: 16px 18px;
        border: 1px solid var(--code-border);
        border-radius: 8px;
        background: var(--code);
        color: #f8fafc;
      }

      .prompt {
        color: var(--accent-strong);
        font-weight: 780;
      }

      .install code {
        display: block;
        min-width: 0;
        overflow-x: auto;
        white-space: nowrap;
      }

      .install a,
      .copy-button {
        border-radius: 8px;
        min-height: 52px;
        padding: 0 18px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border: 0;
        background: var(--accent);
        color: #160700;
        font-weight: 780;
        font: inherit;
        text-decoration: none;
        cursor: pointer;
      }

      .copy-button {
        min-width: 72px;
        min-height: 34px;
        padding: 0 12px;
        border: 1px solid rgba(255, 255, 255, 0.16);
        background: rgba(255, 255, 255, 0.08);
        color: var(--text);
        transition: border-color 160ms ease, background 160ms ease, color 160ms ease;
      }

      .copy-button:hover {
        border-color: rgba(255, 138, 61, 0.54);
        background: rgba(255, 106, 26, 0.16);
      }

      .copy-button.is-copied {
        border-color: rgba(255, 138, 61, 0.7);
        background: rgba(255, 106, 26, 0.22);
        color: #ffd7bf;
      }

      .meta {
        margin-top: 14px;
        color: var(--faint);
        font-size: 13px;
      }

      .demo-shot {
        margin: 24px 0 0;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #0a0b0d;
        overflow: hidden;
      }

      .demo-shot img {
        display: block;
        width: 100%;
        height: auto;
      }

      .shell {
        width: min(var(--max), calc(100% - 32px));
        margin: 0 auto 72px;
        display: grid;
        grid-template-columns: 220px minmax(0, 1fr);
        gap: 34px;
        align-items: start;
      }

      .toc {
        position: sticky;
        top: 88px;
        border-left: 1px solid var(--line);
        padding-left: 16px;
        color: var(--faint);
        font-size: 14px;
      }

      .toc strong {
        display: block;
        color: var(--text);
        margin-bottom: 10px;
      }

      .toc a {
        display: block;
        margin: 8px 0;
        color: var(--muted);
        text-decoration: none;
      }

      .doc {
        min-width: 0;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: rgba(17, 19, 26, 0.78);
        padding: 40px;
      }

      .doc h1 { display: none; }

      .doc h2 {
        margin: 52px 0 14px;
        padding-top: 8px;
        font-size: 28px;
        line-height: 1.12;
        letter-spacing: 0;
      }

      .doc h2:first-child,
      .doc h1 + h2 {
        margin-top: 0;
      }

      .doc h3 {
        margin: 34px 0 10px;
        font-size: 19px;
      }

      .doc p,
      .doc li {
        color: var(--muted);
      }

      .doc blockquote {
        margin: 24px 0;
        padding: 16px 18px;
        border-left: 3px solid var(--accent);
        background: rgba(255, 106, 26, 0.08);
      }

      .doc blockquote p {
        color: var(--text);
        margin: 0;
      }

      .doc pre {
        margin: 16px 0 24px;
        padding: 16px;
        border: 1px solid var(--code-border);
        border-radius: 8px;
        background: var(--code);
        overflow-x: auto;
      }

      .doc code {
        font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
        font-size: 0.92em;
      }

      .doc :not(pre) > code {
        padding: 2px 5px;
        border: 1px solid rgba(255, 255, 255, 0.11);
        border-radius: 5px;
        background: rgba(255, 255, 255, 0.06);
        color: #f8fafc;
      }

      .doc pre code {
        color: #edf2ff;
        white-space: pre;
      }

      .doc table {
        width: 100%;
        border-collapse: collapse;
        margin: 20px 0 28px;
        font-size: 14px;
      }

      .doc th,
      .doc td {
        border: 1px solid var(--line);
        padding: 10px 12px;
        vertical-align: top;
      }

      .doc th {
        color: var(--text);
        background: var(--panel-soft);
        text-align: left;
      }

      .doc ul,
      .doc ol {
        padding-left: 22px;
      }

      .footer {
        width: min(var(--max), calc(100% - 32px));
        margin: 0 auto 44px;
        color: var(--faint);
        font-size: 13px;
      }

      @media (max-width: 820px) {
        .nav { min-height: 58px; }
        .nav-links a:not(.button) { display: none; }
        .hero { padding-top: 48px; }
        .hero p { font-size: 18px; }
        .install { grid-template-columns: 1fr; }
        .shell { grid-template-columns: 1fr; }
        .toc { display: none; }
        .doc { padding: 24px 18px; }
      }
    </style>
  </head>
  <body>
    <header class="site-header">
      <nav class="nav" aria-label="Main">
        <a class="brand" href="/">
          <img class="mark" src="/assets/toolburn-logo.png" alt="" width="30" height="30">
          <span>Toolburn</span>
        </a>
        <div class="nav-links">
          <a href="#install">Install</a>
          <a href="#commands-humans-actually-use">Commands</a>
          <a href="#source-support">Sources</a>
          <a class="button" href="<?php echo htmlspecialchars($repoUrl, ENT_QUOTES, 'UTF-8'); ?>">GitHub</a>
        </div>
      </nav>
    </header>

    <section class="hero">
      <h1>Where did token usage go?</h1>
      <p>Toolburn scans local agent session logs to identify the actors, tools, payloads, recurrence patterns, and context baggage behind the spend.</p>
      <div class="install" id="install">
        <div class="command-box">
          <span class="prompt" aria-hidden="true">$</span>
          <code>curl -fsSL https://raw.githubusercontent.com/DarkExec/ToolBurn/main/install.sh | sh</code>
          <button class="copy-button" type="button" data-copy="curl -fsSL https://raw.githubusercontent.com/DarkExec/ToolBurn/main/install.sh | sh">Copy</button>
        </div>
        <a href="#commands-humans-actually-use">View commands</a>
      </div>
      <figure class="demo-shot">
        <img src="/assets/toolburn-screenshot.png?v=crop1" alt="Toolburn terminal output showing top actors and tools for recent token usage" width="1443" height="431" loading="eager">
      </figure>
      <p class="meta">Rendered from the live README.md. Last README update: <?php echo htmlspecialchars($updatedAt, ENT_QUOTES, 'UTF-8'); ?>.</p>
    </section>

    <main class="shell">
      <aside class="toc" aria-label="Page sections">
        <strong>On this page</strong>
        <a href="#install">Install</a>
        <a href="#start-here">Start Here</a>
        <a href="#commands-humans-actually-use">Commands</a>
        <a href="#source-support">Source Support</a>
        <a href="#current-status">Current Status</a>
      </aside>
      <article class="doc">
        <?php echo $contentHtml; ?>
      </article>
    </main>

    <footer class="footer">
      <span>Toolburn is a DarkExec project. This page is rendered from README.md in DarkExec/ToolBurn.</span>
    </footer>
    <script>
      (() => {
        const copiedText = 'Copied';
        const defaultText = 'Copy';

        async function copyText(text) {
          if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return;
          }
          const textarea = document.createElement('textarea');
          textarea.value = text;
          textarea.setAttribute('readonly', '');
          textarea.style.position = 'fixed';
          textarea.style.top = '-1000px';
          document.body.appendChild(textarea);
          textarea.select();
          document.execCommand('copy');
          textarea.remove();
        }

        function flash(button) {
          button.textContent = copiedText;
          button.classList.add('is-copied');
          window.setTimeout(() => {
            button.textContent = defaultText;
            button.classList.remove('is-copied');
          }, 1400);
        }

        document.addEventListener('click', async (event) => {
          const button = event.target.closest('.copy-button');
          if (!button) {
            return;
          }
          try {
            await copyText(button.dataset.copy || '');
            flash(button);
          } catch (error) {
            button.textContent = 'Failed';
            window.setTimeout(() => {
              button.textContent = defaultText;
            }, 1400);
          }
        });
      })();
    </script>
  </body>
</html>
