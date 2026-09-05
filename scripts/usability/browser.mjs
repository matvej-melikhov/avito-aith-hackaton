// Usage: node scripts/usability/browser.mjs actor-name '{"action":"snapshot"}'
const [actor, payload] = process.argv.slice(2);
if(!actor || !payload) throw new Error('Provide actor name and a JSON UI action');
const response=await fetch('http://127.0.0.1:18180',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...JSON.parse(payload),actor})});
console.log(await response.text());
if(!response.ok)process.exitCode=1;
