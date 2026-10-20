import { createServer } from 'vite';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');

async function run() {
  const server = await createServer({
    root: repoRoot,
    server: { middlewareMode: true },
  });
  try {
    await server.ssrLoadModule('/test_apply_to_all.ts');
    await server.ssrLoadModule('/test_text_caption_separation.ts');
    await server.ssrLoadModule('/test_editor_repair_integrity.ts');
    await server.ssrLoadModule('/frontend/test_camera_interactions.js');
    await server.ssrLoadModule('/frontend/test_studio_render_path.js');
  } catch (err) {
    console.error('Test execution failed:', err);
    process.exit(1);
  } finally {
    await server.close();
  }
}

run();
