// Pull the FSAI member directory with the page's own search, for 56_fsai_directory.py.
// Run in the browser console on https://mms.fsai.org/members/directory/search_bootstrap.php?org_id=FSAN
// It saves fsai_raw.json as a browser download when done.
// One empty search (every entry), then one search per Classification and per Type, paging through
// each at about one request a second. Product Category alone is ignored by the server (it returns
// every entry, checked 2026-11-04), so it is not searched. Progress is in window._fsai.
window._fsai = {members: {}, tags: {}, log: [], done: false, err: null, searches: 0, pages: 0};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const post = (b, s, sv) => new Promise((res, rej) => $.ajax({
  type: 'POST', url: 'search_bootstrap_ajax.php?org_id=FSAN',
  data: {buttonNum: b, searchValues: sv, two_column: '', sameSearch: s}, dataType: 'html'
}).done(res).fail(x => rej('HTTP ' + x.status)));
const parse = h => {
  const d = new DOMParser().parseFromString(h, 'text/html');
  const nums = [...d.querySelectorAll('[data-buttonnum]')]
    .map(e => parseInt((e.getAttribute('data-buttonnum') || '').replace(/\D/g, ''))).filter(n => n > 0);
  const out = [...d.querySelectorAll('div.search-result')].map(c => {
    const t = s => { const e = c.querySelector(s); return e ? e.innerText.trim() : ''; };
    const links = [...c.querySelectorAll('a')].map(a => ({text: a.textContent.trim(), href: a.getAttribute('href') || ''}));
    const web = links.find(l => /^https?:\/\//i.test(l.text) || /website/i.test(l.text) && /^https?:/.test(l.href));
    return {id: c.id.replace('sr-', ''), name: t('.name-plate'), second: t('.second-line-name'),
            address: [...c.querySelectorAll('.address-block > div')].map(x => x.innerText.trim()).filter(Boolean),
            phone: t('.phone-number').replace(/^Primary Phone:\s*/i, ''),
            website: web ? (/^https?:/i.test(web.text) ? web.text : web.href) : '',
            more: t('.more-info').slice(0, 300), text: c.innerText.replace(/\s+/g, ' ').trim().slice(0, 600)};
  });
  return {out, last: nums.length ? Math.max(...nums) : 1};
};
const runSearch = async (label, overrides) => {
  const sv = Object.assign(getSearchValues(), overrides);
  const ids = new Set();
  let h = await post('B1', 0, sv); window._fsai.searches++;
  let p = parse(h); const last = p.last;
  for (let n = 1; ; n++) {
    if (n > 1) { await sleep(900); h = await post('B' + n, 1, sv); p = parse(h); }
    window._fsai.pages++;
    for (const m of p.out) { if (!window._fsai.members[m.id]) window._fsai.members[m.id] = m; ids.add(m.id); }
    if (n >= last || p.out.length === 0) break;
  }
  if (label) window._fsai.tags[label] = [...ids];
  window._fsai.log.push(label + ': ' + ids.size + ' (' + last + ' pages)');
  await sleep(900);
};
const opts = name => [...document.querySelectorAll('select[name=' + name + '] option')]
  .filter(o => o.value && o.value !== '0').map(o => [o.value, o.text.trim()]);
(async () => {
  try {
    await runSearch('ALL', {});
    for (const [v, t] of opts('dynamic_field_437')) await runSearch('class:' + t, {dynamic_field_437: v});
    for (const [v, t] of opts('product_type')) await runSearch('type:' + t + '#' + v, {product_type: v});
    window._fsai.done = true;
    const body = JSON.stringify({pulled: new Date().toISOString(), members: window._fsai.members, tags: window._fsai.tags});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([body], {type: 'application/json'}));
    a.download = 'fsai_raw.json'; document.body.appendChild(a); a.click(); a.remove();
  } catch (e) { window._fsai.err = String(e); }
})();
