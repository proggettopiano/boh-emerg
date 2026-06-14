import { sanitizeSearchText } from './src/lib/searchText.js';
const queries = ["l'amore", "l’amore", "Gesù disse: \"com'è\"", "~ 542 ~"];
for (const q of queries) {
  console.log('INPUT:', q, '->', sanitizeSearchText(q));
}
const sample = "L’amore è qui e com'è bello";
const query = "com'è";
const queryPattern = query.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&").replace(/'/g, "['’]");
const re = new RegExp(`(${queryPattern})`, 'ig');
console.log('RE:', re);
console.log('HIGHLIGHT:', sample.replace(re, (m) => `<mark>${m}</mark>`));
