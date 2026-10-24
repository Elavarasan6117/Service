import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const indexPath = resolve(frontendRoot, 'dist/index.html')
const html = await readFile(indexPath, 'utf8')

// Render may deploy the static site without applying the Blueprint's SPA
// rewrite rules. Publish real directory entry points for the app's main pages
// so direct visits and refreshes still load React Router.
for (const route of ['locations', 'admin']) {
  const routeIndexPath = resolve(frontendRoot, `dist/${route}/index.html`)
  await mkdir(dirname(routeIndexPath), { recursive: true })
  await writeFile(routeIndexPath, html)
}
