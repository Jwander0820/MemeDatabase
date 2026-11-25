"use strict";
byId("filters").onsubmit = event => { event.preventDefault(); search(true); };
byId("query").oninput = () => {
  ++listRequest;
  clearTimeout(timer);
  more.hidden = true;
  timer = setTimeout(() => search(true), 300);
};
byId("sort").onchange = () => search(true);
more.onclick = () => search(retryFromStart);
renderScope();
search(true);
showRoute();
