import { createServer } from 'vite';

async function run() {
  const server = await createServer({
    root: '.',
    server: { middlewareMode: true },
  });
  try {
    await server.ssrLoadModule('../test_apply_to_all.ts');
    await server.ssrLoadModule('../test_text_caption_separation.ts');
    await server.ssrLoadModule('./test_camera_interactions.js');
  } catch (err) {
    console.error('Test execution failed:', err);
    process.exit(1);
  } finally {
    await server.close();
  }
}

run();
