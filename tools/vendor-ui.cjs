const fs = require('node:fs');
fs.mkdirSync('static/vendor', {recursive: true});
fs.copyFileSync('node_modules/lucide/dist/umd/lucide.js', 'static/vendor/lucide.js');
fs.copyFileSync('node_modules/chart.js/dist/chart.umd.js', 'static/vendor/chart.js');
fs.copyFileSync('node_modules/lucide/LICENSE', 'static/vendor/LUCIDE_LICENSE');
fs.copyFileSync('node_modules/chart.js/LICENSE.md', 'static/vendor/CHART_LICENSE');
