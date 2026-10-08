'use strict';
// Model output is untrusted. Keep only presentation markup; never execute HTML,
// load remote images, or permit model-created controls or application IDs.
function renderMarkdown(target, source) {
  const html=marked.parse(source,{gfm:true,breaks:false});
  target.innerHTML=DOMPurify.sanitize(html,{
    ALLOWED_TAGS:['p','br','hr','h1','h2','h3','h4','h5','h6','blockquote','ul','ol','li',
      'strong','em','del','s','code','pre','a','table','thead','tbody','tr','th','td','div','span','sup','sub'],
    ALLOWED_ATTR:['href','title','start','align'],ALLOW_DATA_ATTR:false,ALLOW_ARIA_ATTR:false
  });
  target.querySelectorAll('a').forEach(a=>{
    const href=a.getAttribute('href')||'';
    if(!/^(https?:\/\/|mailto:|#)/i.test(href))a.removeAttribute('href');
    a.rel='noopener noreferrer';a.target='_blank';
  });
  target.querySelectorAll('table').forEach(table=>{const wrapper=document.createElement('div');wrapper.className='markdown-table';table.replaceWith(wrapper);wrapper.append(table);});
  target.querySelectorAll('pre').forEach(pre=>{
    const code=pre.querySelector('code');if(!code)return;
    const button=document.createElement('button');button.className='copy-code';button.type='button';button.textContent='Copy code';
    button.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(code.textContent);button.textContent='Copied';}catch{button.textContent='Select code to copy';}});
    pre.prepend(button);
  });
}
