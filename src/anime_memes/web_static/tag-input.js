"use strict";
// 標籤膠囊輸入：把 name="tags" 的文字框換成膠囊 + 自動完成，送出時仍是逗號分隔字串，後端不需改動。
// 依標籤名稱與別名比對分類清單；分類外的新標籤也允許，但會標示提醒。
(() => {
  const SEPARATORS = /[,，、;；\n]/;
  let catalogPromise = null;

  function loadCatalog(force = false) {
    if (!catalogPromise || force) {
      catalogPromise = fetch("/api/tags/catalog")
        .then((response) => (response.ok ? response.json() : Promise.reject(new Error("catalog"))))
        .catch(() => ({categories: [], tags: [], max_tags_per_entry: 6}));
    }
    return catalogPromise;
  }

  function split(value) {
    return String(value || "").split(SEPARATORS).map((part) => part.trim()).filter(Boolean);
  }

  function create(tag, attrs = {}, text = "") {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
    if (text) node.textContent = text;
    return node;
  }

  let uid = 0;

  function mount(original) {
    if (!original || original.tagInput) return original?.tagInput;
    const id = `tag-input-${++uid}`;
    const hidden = create("input", {type: "hidden", name: original.name});
    const root = create("div", {class: "tag-input"});
    const chips = create("div", {class: "tag-input-chips"});
    const field = create("input", {
      type: "text", class: "tag-input-field", autocomplete: "off", role: "combobox",
      "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": `${id}-list`,
      placeholder: "輸入標籤，Enter 加入",
    });
    const list = create("ul", {class: "tag-suggest", id: `${id}-list`, role: "listbox"});
    const hint = create("p", {class: "tag-input-hint", "aria-live": "polite"});
    list.hidden = true;
    chips.append(field);
    root.append(chips, list);
    // 沿用原欄位的 label 文字給輸入框，再把原欄位換成隱藏欄位。
    field.setAttribute("aria-label", original.closest("label")?.querySelector("span")?.textContent || "標籤");
    original.replaceWith(hidden, root, hint);

    let values = [];
    let options = [];
    let active = -1;
    let catalog = {categories: [], tags: [], max_tags_per_entry: 6};
    const categoryLabel = (idValue) => catalog.categories.find((c) => c.id === idValue)?.label || "分類外";
    const known = (name) => catalog.tags.find((tag) => tag.name === name);

    function sync() {
      hidden.value = values.join(", ");
      chips.querySelectorAll(".tag-chip").forEach((node) => node.remove());
      values.forEach((value) => {
        const tag = known(value);
        const chip = create("span", {class: `tag-chip${tag && tag.in_taxonomy ? "" : " outside"}`,
          title: tag?.description || (tag ? "" : "分類外的新標籤")}, value);
        const remove = create("button", {type: "button", "aria-label": `移除 ${value}`}, "×");
        remove.addEventListener("click", () => {
          values = values.filter((item) => item !== value);
          sync();
          field.focus();
        });
        chip.append(remove);
        chips.insertBefore(chip, field);
      });
      const max = catalog.max_tags_per_entry || 6;
      const outside = values.filter((value) => !known(value)?.in_taxonomy);
      hint.textContent = [
        values.length > max ? `已有 ${values.length} 個標籤，分類建議每張最多 ${max} 個。` : "",
        outside.length ? `分類外：${outside.join("、")}（會以舊版保留標籤儲存）。` : "",
      ].filter(Boolean).join(" ");
    }

    function add(name) {
      // 輸入別名時存成正式名稱，例如「毒舌」→「嗆人」。
      const canonical = catalog.tags.find((tag) => tag.name === name || tag.aliases.includes(name))?.name || name;
      if (canonical && !values.includes(canonical)) values.push(canonical);
      field.value = "";
      close();
      sync();
    }

    function close() {
      list.hidden = true;
      active = -1;
      field.setAttribute("aria-expanded", "false");
      field.removeAttribute("aria-activedescendant");
    }

    function render() {
      const query = field.value.trim().toLowerCase();
      const scored = catalog.tags
        .filter((tag) => !values.includes(tag.name))
        .map((tag) => {
          const names = [tag.name, ...tag.aliases].map((n) => n.toLowerCase());
          let score = query ? -1 : 1;
          if (query) {
            if (names.some((n) => n === query)) score = 3;
            else if (names.some((n) => n.startsWith(query))) score = 2;
            else if (names.some((n) => n.includes(query))) score = 1;
          }
          const alias = query ? tag.aliases.find((a) => a.toLowerCase().includes(query)) : "";
          return {tag, score, alias};
        })
        .filter((item) => item.score > 0)
        .sort((a, b) => b.score - a.score || Number(b.tag.in_taxonomy) - Number(a.tag.in_taxonomy) || b.tag.count - a.tag.count)
        .slice(0, 8);
      options = scored.map(({tag, alias}) => ({name: tag.name, alias, tag}));
      const raw = field.value.trim();
      if (raw && !catalog.tags.some((tag) => tag.name === raw || tag.aliases.includes(raw)) && !values.includes(raw)) {
        options.push({name: raw, custom: true});
      }
      list.replaceChildren(...options.map((option, index) => {
        const item = create("li", {role: "option", id: `${id}-opt-${index}`, "aria-selected": String(index === active)});
        if (option.custom) {
          item.append(create("strong", {}, `新增「${option.name}」`), create("small", {}, "分類外"));
        } else {
          item.append(create("strong", {}, option.name));
          const meta = [option.alias ? `別名：${option.alias}` : "", categoryLabel(option.tag.category), `${option.tag.count} 張`];
          item.append(create("small", {}, meta.filter(Boolean).join(" · ")));
        }
        // mousedown 先於輸入框 blur，避免選項還沒點到清單就關閉。
        item.addEventListener("mousedown", (event) => {
          event.preventDefault();
          add(option.name);
        });
        return item;
      }));
      list.hidden = !options.length;
      field.setAttribute("aria-expanded", String(!list.hidden));
      if (active >= 0 && options[active]) field.setAttribute("aria-activedescendant", `${id}-opt-${active}`);
      else field.removeAttribute("aria-activedescendant");
    }

    field.addEventListener("input", () => {
      // 貼上「a, b, c」時一次拆成多個標籤。
      if (SEPARATORS.test(field.value)) {
        const parts = split(field.value);
        const last = /[,，、;；\n]\s*$/.test(field.value) ? "" : parts.pop() || "";
        parts.forEach(add);
        field.value = last;
      }
      active = -1;
      render();
    });
    field.addEventListener("focus", render);
    field.addEventListener("blur", () => setTimeout(close, 100));
    field.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (list.hidden) render();
        if (!options.length) return;
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        active = (active + step + options.length) % options.length;
        render();
      } else if (event.key === "Enter" || (event.key === "Tab" && field.value.trim())) {
        const choice = active >= 0 ? options[active]?.name : field.value.trim();
        if (choice) {
          event.preventDefault();
          add(choice);
        }
      } else if (event.key === "Escape" && !list.hidden) {
        // 只關掉建議清單，不關閉整個抽屜或對話框。
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === "Backspace" && !field.value && values.length) {
        values.pop();
        sync();
      }
    });
    root.addEventListener("click", (event) => {
      if (event.target === root || event.target === chips) field.focus();
    });

    const api = {
      setValue(next) {
        values = [...new Set(split(Array.isArray(next) ? next.join(",") : next))];
        field.value = "";
        close();
        sync();
      },
      // 送出前把輸入框裡還沒按 Enter 的文字也算進去。
      commit() {
        if (field.value.trim()) add(field.value.trim());
        return hidden.value;
      },
      refresh() {
        return loadCatalog(true).then((data) => { catalog = data; sync(); });
      },
    };
    hidden.tagInput = api;
    api.setValue(original.value);
    loadCatalog().then((data) => { catalog = data; sync(); });
    return api;
  }

  window.MemeTagInput = {mount, refresh: () => loadCatalog(true)};
})();
