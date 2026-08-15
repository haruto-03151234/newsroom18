(() => {
  "use strict";

  const TIMEZONE = "Asia/Tokyo";
  const CATEGORY_META = {
    domestic: { label: "国内", english: "JAPAN" },
    world: { label: "海外", english: "WORLD" },
    technology: { label: "テクノロジー", english: "TECHNOLOGY" },
    entertainment: { label: "エンタメ", english: "ENTERTAINMENT" },
    sports: { label: "スポーツ", english: "SPORTS" },
    economy: { label: "経済", english: "BUSINESS" },
    science: { label: "科学", english: "SCIENCE" },
    society: { label: "社会", english: "SOCIETY" },
    other: { label: "その他", english: "MORE" }
  };
  const CATEGORY_ORDER = [
    "domestic",
    "world",
    "technology",
    "economy",
    "science",
    "entertainment",
    "sports",
    "society",
    "other"
  ];
  const CATEGORY_ALIASES = {
    "国内": "domestic",
    japan: "domestic",
    national: "domestic",
    "海外": "world",
    international: "world",
    global: "world",
    "テクノロジー": "technology",
    tech: "technology",
    it: "technology",
    "エンタメ": "entertainment",
    entertainment: "entertainment",
    culture: "entertainment",
    "スポーツ": "sports",
    sport: "sports",
    "経済": "economy",
    business: "economy",
    finance: "economy",
    "科学": "science",
    "社会": "society"
  };

  const FALLBACK_DATA = {
    schemaVersion: 1,
    site: {
      name: "NEWSROOM 18",
      timezone: TIMEZONE,
      description: "朝6時・昼12時・夕方18時に更新するニュースダイジェスト"
    },
    generatedAt: "2026-08-15T06:00:00+09:00",
    summary: "表示確認用のサンプル号です。自動更新後は、対象時間帯のニュースに置き換わります。",
    edition: { id: "sample-0600", label: "朝刊・サンプル", slot: "06:00", isSample: true },
    coverage: { start: "2026-08-14T18:00:00+09:00", end: "2026-08-15T06:00:00+09:00" },
    articles: [
      {
        id: "sample-top",
        slug: "sample-top",
        title: "表示サンプル：最も重要なニュースが、この大見出しに掲載されます",
        dek: "複数の信頼できる情報源を照合し、出来事・影響・今後の注目点を短く整理します。",
        summary: "これはレイアウト確認用の記事です。実際のニュースではありません。",
        category: "domestic",
        importance: 5,
        publishedAt: "2026-08-15T05:20:00+09:00",
        updatedAt: "2026-08-15T05:50:00+09:00",
        body: [
          "これはデータを読み込めない環境でも画面を確認できるように用意したサンプル記事です。実際のニュース内容を示すものではありません。",
          "自動更新が始まると、対象時間帯のニュースを複数ソースで確認し、重複をまとめた記事がここに表示されます。"
        ],
        sources: [
          { name: "NHK NEWS WEB（出典表示例）", url: "https://www3.nhk.or.jp/news/" },
          { name: "Reuters（出典表示例）", url: "https://www.reuters.com/" }
        ]
      },
      {
        id: "sample-world",
        slug: "sample-world",
        title: "海外ニュースの見出しと要点を、背景が分かる長さで表示",
        summary: "各記事には更新時刻と参照した情報源へのリンクが付きます。",
        category: "world",
        importance: 4,
        publishedAt: "2026-08-15T04:40:00+09:00",
        updatedAt: "2026-08-15T05:35:00+09:00",
        sources: [{ name: "BBC News（出典表示例）", url: "https://www.bbc.com/news" }]
      },
      {
        id: "sample-tech",
        slug: "sample-tech",
        title: "テクノロジー分野の最新動向を、専門用語を抑えて解説",
        summary: "製品発表、AI、サイバーセキュリティなどの重要な動きを整理します。",
        category: "technology",
        importance: 3,
        publishedAt: "2026-08-15T03:30:00+09:00",
        updatedAt: "2026-08-15T05:10:00+09:00",
        sources: [{ name: "企業公式発表（出典表示例）", url: "https://example.com/" }]
      },
      {
        id: "sample-entertainment",
        slug: "sample-entertainment",
        title: "エンタメの注目トピックも同じフォーマットで掲載",
        summary: "作品・イベント・文化に関する話題を簡潔にまとめます。",
        category: "entertainment",
        importance: 2,
        publishedAt: "2026-08-15T02:10:00+09:00",
        updatedAt: "2026-08-15T04:55:00+09:00",
        sources: [{ name: "公式サイト（出典表示例）", url: "https://example.com/" }]
      },
      {
        id: "sample-sports",
        slug: "sample-sports",
        title: "スポーツは試合結果と重要な記録をひと目で確認",
        summary: "大会公式情報と報道を参照し、主要な結果をまとめます。",
        category: "sports",
        importance: 3,
        publishedAt: "2026-08-15T01:20:00+09:00",
        updatedAt: "2026-08-15T04:25:00+09:00",
        sources: [{ name: "大会公式サイト（出典表示例）", url: "https://example.com/" }]
      }
    ]
  };

  const state = {
    data: null,
    archive: [],
    selectedCategory: "all",
    usedFallback: false
  };

  const dom = {};

  document.addEventListener("DOMContentLoaded", initialize);

  async function initialize() {
    cacheDom();
    dom.retryButton.addEventListener("click", () => window.location.reload());
    dom.copyLinkButton.addEventListener("click", copyCurrentUrl);
    window.addEventListener("popstate", route);

    const [latestResult, archiveResult] = await Promise.allSettled([
      fetchJson("data/latest.json"),
      fetchJson("data/archive.json")
    ]);

    let rawData;
    if (latestResult.status === "fulfilled") {
      rawData = latestResult.value;
    } else {
      rawData = FALLBACK_DATA;
      state.usedFallback = true;
    }

    try {
      state.data = normalizeEdition(rawData);
    } catch (error) {
      console.error("Invalid news data:", error);
      state.data = normalizeEdition(FALLBACK_DATA);
      state.usedFallback = true;
    }

    state.archive = archiveResult.status === "fulfilled"
      ? normalizeArchive(archiveResult.value)
      : [];
    if (!state.archive.length) {
      state.archive = [editionToArchiveItem(state.data)];
    }

    dom.loadingState.hidden = true;
    dom.sampleNotice.hidden = !(state.usedFallback || state.data.edition.isSample);
    await route();
  }

  function cacheDom() {
    const ids = [
      "loading-state", "error-state", "error-message", "retry-button", "sample-notice",
      "mode-notice-title", "mode-notice-message",
      "dashboard-view", "article-view", "edition-date", "edition-label", "edition-coverage",
      "last-updated", "brief-summary", "lead-article", "bulletin-list", "category-filters",
      "important-grid", "news-sections", "result-count", "empty-state", "archive-list",
      "article-breadcrumb", "article-category", "article-importance", "article-title",
      "article-dek", "article-updated", "article-body", "article-sources-list",
      "copy-link-button", "copy-status", "related-grid"
    ];
    for (const id of ids) {
      dom[toCamel(id)] = document.getElementById(id);
    }
  }

  async function route() {
    hideError();
    const params = new URLSearchParams(window.location.search);
    const editionId = params.get("edition");
    const articleId = params.get("article");

    if (editionId && editionId !== state.data.edition.id) {
      const archiveItem = state.archive.find((item) => item.id === editionId);
      if (archiveItem?.dataUrl) {
        try {
          state.data = normalizeEdition(await fetchJson(archiveItem.dataUrl));
        } catch (error) {
          showError("指定した号を読み込めませんでした。最新号を表示します。", false);
        }
      } else if (archiveItem) {
        showError("この号の記事データはまだ公開されていません。最新号を表示します。", false);
      }
    }

    renderChrome(state.data);
    renderArchive(state.archive, state.data.edition.id);

    if (articleId) {
      const article = await resolveArticle(articleId);
      if (article) {
        renderArticle(article);
      } else {
        showError("指定した記事が見つかりませんでした。最新号を表示します。", false);
        renderDashboard();
      }
      return;
    }

    renderDashboard(params.get("category") || "all");
  }

  async function resolveArticle(articleId) {
    let article = state.data.articles.find((item) => item.id === articleId || item.slug === articleId);
    if (article) return article;

    if (!/^[a-zA-Z0-9._-]+$/.test(articleId)) return null;
    try {
      const remote = await fetchJson(`data/articles/${encodeURIComponent(articleId)}.json`);
      article = normalizeArticle(remote.article || remote, 0);
      return article;
    } catch (_error) {
      return null;
    }
  }

  function renderDashboard(requestedCategory = "all") {
    dom.articleView.hidden = true;
    dom.dashboardView.hidden = false;

    const available = new Set(state.data.articles.map((article) => article.category));
    const validFilters = new Set(["all", "important", ...available]);
    state.selectedCategory = validFilters.has(requestedCategory) ? requestedCategory : "all";

    renderLead();
    renderBulletin();
    renderFilters(available);
    renderImportant();
    renderNewsSections();
    updateDocumentMeta();
  }

  function renderChrome(data) {
    dom.editionDate.textContent = formatDate(data.generatedAt, { year: "numeric", month: "long", day: "numeric", weekday: "short" });
    dom.editionLabel.textContent = data.edition.label;
    dom.editionCoverage.textContent = formatCoverage(data.coverage);
    setTime(dom.lastUpdated, data.generatedAt, formatDate(data.generatedAt, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }));
    dom.briefSummary.textContent = data.summary || `${data.articles.length}本のニュースを掲載しています。`;
    const simpleMode = data.generationMode === "fallback" && !data.edition.isSample;
    dom.sampleNotice.hidden = !(state.usedFallback || data.edition.isSample || simpleMode);
    if (simpleMode) {
      dom.modeNoticeTitle.textContent = "簡易編集モード";
      dom.modeNoticeMessage.textContent = "現在は出典と見出しを中心に掲載しています。詳しい内容は各出典で確認してください。";
    } else {
      dom.modeNoticeTitle.textContent = "表示確認用サンプル";
      dom.modeNoticeMessage.textContent = "自動更新が始まると、この内容は実際のニュースに置き換わります。";
    }
  }

  function renderLead() {
    clear(dom.leadArticle);
    const article = sortedByImportance(state.data.articles)[0];
    if (!article) {
      dom.leadArticle.append(element("p", "", "現在、掲載できるニュースはありません。"));
      return;
    }

    dom.leadArticle.append(
      element("span", "lead-article__category", categoryLabel(article.category)),
      headingLink(article, "h2"),
      element("p", "lead-article__dek", article.dek || article.summary),
      articleMeta(article, "lead-article__meta")
    );
  }

  function renderBulletin() {
    clear(dom.bulletinList);
    const leadId = sortedByImportance(state.data.articles)[0]?.id;
    const articles = [...state.data.articles]
      .filter((article) => article.id !== leadId)
      .sort((a, b) => dateValue(b.updatedAt) - dateValue(a.updatedAt))
      .slice(0, 5);

    for (const article of articles) {
      const item = document.createElement("li");
      const link = articleLink(article, article.title);
      const time = document.createElement("time");
      time.dateTime = article.updatedAt;
      time.textContent = `${formatTime(article.updatedAt)} 更新・${categoryLabel(article.category)}`;
      item.append(link, time);
      dom.bulletinList.append(item);
    }
  }

  function renderFilters(available) {
    clear(dom.categoryFilters);
    const filters = [
      { id: "all", label: "すべて" },
      { id: "important", label: "重要" },
      ...CATEGORY_ORDER.filter((id) => available.has(id)).map((id) => ({ id, label: categoryLabel(id) }))
    ];

    for (const filter of filters) {
      const button = element("button", "filter-button", filter.label);
      button.type = "button";
      button.setAttribute("aria-pressed", String(filter.id === state.selectedCategory));
      button.addEventListener("click", () => selectCategory(filter.id));
      dom.categoryFilters.append(button);
    }
  }

  function selectCategory(category) {
    state.selectedCategory = category;
    const url = new URL(window.location.href);
    url.searchParams.delete("article");
    url.searchParams.delete("edition");
    if (category === "all") url.searchParams.delete("category");
    else url.searchParams.set("category", category);
    window.history.replaceState({}, "", url);
    renderFilters(new Set(state.data.articles.map((article) => article.category)));
    renderImportant();
    renderNewsSections();
    document.getElementById("all-news-heading")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderImportant() {
    clear(dom.importantGrid);
    let articles = sortedByImportance(state.data.articles).filter((article) => article.importance >= 4);
    if (state.selectedCategory === "important") {
      articles = articles.slice(0, 6);
    } else if (state.selectedCategory !== "all") {
      articles = articles.filter((article) => article.category === state.selectedCategory);
    }
    articles.slice(0, 4).forEach((article) => dom.importantGrid.append(storyCard(article, true)));
    dom.importantGrid.parentElement.hidden = !articles.length;
  }

  function renderNewsSections() {
    clear(dom.newsSections);
    let articles = state.data.articles;
    if (state.selectedCategory === "important") {
      articles = articles.filter((article) => article.importance >= 4);
    } else if (state.selectedCategory !== "all") {
      articles = articles.filter((article) => article.category === state.selectedCategory);
    }

    dom.resultCount.textContent = `${articles.length}本を表示`;
    dom.emptyState.hidden = Boolean(articles.length);

    for (const category of CATEGORY_ORDER) {
      const categoryArticles = articles
        .filter((article) => article.category === category)
        .sort((a, b) => dateValue(b.updatedAt) - dateValue(a.updatedAt));
      if (!categoryArticles.length) continue;

      const section = element("section", "category-section");
      section.setAttribute("aria-labelledby", `category-${category}`);
      const heading = element("div", "category-section__heading");
      const title = element("h3", "", categoryLabel(category));
      title.id = `category-${category}`;
      heading.append(title, element("span", "", CATEGORY_META[category]?.english || "NEWS"));
      const grid = element("div", "story-grid");
      categoryArticles.forEach((article) => grid.append(storyCard(article)));
      section.append(heading, grid);
      dom.newsSections.append(section);
    }
  }

  function storyCard(article, important = false) {
    const card = element("article", important ? "story-card story-card--important" : "story-card");
    const category = element("span", "story-card__category", categoryLabel(article.category));
    const title = headingLink(article, "h3");
    const summary = element("p", "story-card__summary", article.summary || article.dek);
    const meta = articleMeta(article, "story-meta");
    const sourceNames = article.sources.map((source) => source.name).join(" / ");
    const sources = element("p", "story-card__sources", `出典：${sourceNames || "確認中"}`);
    card.append(category, title, summary, meta, sources);
    return card;
  }

  function renderArchive(items, currentEditionId) {
    clear(dom.archiveList);
    for (const item of items.slice(0, 12)) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = queryUrl({ edition: item.id });
      if (item.id === currentEditionId) link.setAttribute("aria-current", "page");
      const date = document.createElement("time");
      date.dateTime = item.generatedAt;
      date.textContent = formatDate(item.generatedAt, { month: "2-digit", day: "2-digit" });
      const title = element("strong", "", item.label);
      const count = element("span", "", `${item.articleCount ?? "—"}本・${formatTime(item.generatedAt)}更新`);
      link.append(date, title, count);
      li.append(link);
      dom.archiveList.append(li);
    }
  }

  function renderArticle(article) {
    dom.dashboardView.hidden = true;
    dom.articleView.hidden = false;
    dom.articleBreadcrumb.textContent = article.title;
    dom.articleCategory.textContent = categoryLabel(article.category);
    dom.articleImportance.textContent = article.importance >= 4 ? `重要度 ${article.importance}/5` : "";
    dom.articleTitle.textContent = article.title;
    dom.articleDek.textContent = article.dek || article.summary;
    setTime(dom.articleUpdated, article.updatedAt, formatDate(article.updatedAt, {
      year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit"
    }));
    dom.copyStatus.textContent = "";

    renderArticleBody(article);
    renderSources(article.sources);
    renderRelated(article);
    updateDocumentMeta(article);
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function renderArticleBody(article) {
    clear(dom.articleBody);
    if (article.sections.length) {
      article.sections.forEach((section) => {
        if (section.heading) dom.articleBody.append(element("h2", "", section.heading));
        section.paragraphs.forEach((paragraph) => dom.articleBody.append(element("p", "", paragraph)));
      });
      return;
    }
    const paragraphs = article.body.length
      ? article.body
      : [article.summary || article.dek || "記事本文を準備しています。"];
    paragraphs.forEach((paragraph) => dom.articleBody.append(element("p", "", paragraph)));
  }

  function renderSources(sources) {
    clear(dom.articleSourcesList);
    if (!sources.length) {
      dom.articleSourcesList.append(element("li", "", "情報源を確認中です。"));
      return;
    }
    for (const source of sources) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = safeExternalUrl(source.url);
      link.target = "_blank";
      link.rel = "noopener noreferrer nofollow";
      link.textContent = source.name;
      li.append(link);
      if (source.publishedAt) {
        const time = document.createElement("time");
        time.dateTime = source.publishedAt;
        time.textContent = `（${formatDate(source.publishedAt, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}）`;
        li.append(time);
      }
      dom.articleSourcesList.append(li);
    }
  }

  function renderRelated(article) {
    clear(dom.relatedGrid);
    const related = state.data.articles
      .filter((candidate) => candidate.id !== article.id)
      .sort((a, b) => {
        const sameA = a.category === article.category ? 1 : 0;
        const sameB = b.category === article.category ? 1 : 0;
        return sameB - sameA || b.importance - a.importance || dateValue(b.updatedAt) - dateValue(a.updatedAt);
      })
      .slice(0, 3);
    related.forEach((candidate) => dom.relatedGrid.append(storyCard(candidate)));
    dom.relatedGrid.parentElement.hidden = !related.length;
  }

  function articleMeta(article, className) {
    const meta = element("div", className);
    const time = document.createElement("time");
    time.dateTime = article.updatedAt;
    time.textContent = `${formatTime(article.updatedAt)} 更新`;
    const sources = element("span", "source-count", `${article.sources.length}ソース`);
    meta.append(time, sources);
    return meta;
  }

  function headingLink(article, tagName) {
    const heading = document.createElement(tagName);
    heading.append(articleLink(article, article.title));
    return heading;
  }

  function articleLink(article, text) {
    const link = document.createElement("a");
    link.href = queryUrl({ article: article.slug || article.id });
    link.textContent = text;
    return link;
  }

  function normalizeEdition(raw) {
    if (!raw || typeof raw !== "object") throw new Error("News data must be an object");
    const rawArticles = Array.isArray(raw.articles)
      ? raw.articles
      : Array.isArray(raw.items)
        ? raw.items
        : Array.isArray(raw.stories)
          ? raw.stories
          : [];
    const articles = deduplicate(rawArticles.map(normalizeArticle));
    const generatedAt = validDate(raw.generatedAt || raw.updatedAt) || new Date().toISOString();
    const edition = raw.edition && typeof raw.edition === "object" ? raw.edition : {};
    const id = String(edition.id || raw.id || generatedAt.slice(0, 16).replace(/[-T:]/g, ""));

    return {
      schemaVersion: raw.schemaVersion || 1,
      site: raw.site || {},
      generatedAt,
      summary: String(raw.summary || raw.description || ""),
      generationMode: String(raw.generationMode || ""),
      edition: {
        id,
        label: String(edition.label || edition.name || inferEditionLabel(generatedAt)),
        slot: String(edition.slot || formatTime(generatedAt)),
        isSample: Boolean(edition.isSample || raw.isSample)
      },
      coverage: {
        start: validDate(raw.coverage?.start || raw.coverage?.from || raw.windowStart),
        end: validDate(raw.coverage?.end || raw.coverage?.to || raw.windowEnd)
      },
      articles
    };
  }

  function normalizeArticle(article, index) {
    const item = article && typeof article === "object" ? article : {};
    const title = String(item.title || item.headline || `無題の記事 ${index + 1}`);
    const id = String(item.id || item.slug || slugify(title) || `story-${index + 1}`);
    const slug = String(item.slug || id);
    const publishedAt = validDate(item.publishedAt || item.published_at || item.date) || new Date().toISOString();
    const updatedAt = validDate(item.updatedAt || item.updated_at) || publishedAt;
    const sources = normalizeSources(item.sources || item.sourceLinks || item.references || []);
    const body = normalizeParagraphs(item.body || item.content || []);
    const sections = normalizeSections(item.sections || []);

    return {
      id,
      slug,
      title,
      dek: String(item.dek || item.subtitle || item.lead || ""),
      summary: String(item.summary || item.description || item.dek || ""),
      category: normalizeCategory(item.category || item.section || "other"),
      importance: clamp(Number(item.importance ?? item.priority ?? 3), 1, 5),
      publishedAt,
      updatedAt,
      sources,
      body,
      sections,
      tags: Array.isArray(item.tags) ? item.tags.map(String) : []
    };
  }

  function normalizeSources(value) {
    const list = Array.isArray(value) ? value : [value];
    return list
      .map((source, index) => {
        if (typeof source === "string") {
          return { name: `情報源 ${index + 1}`, url: source, publishedAt: "" };
        }
        if (!source || typeof source !== "object") return null;
        return {
          name: String(source.name || source.publisher || source.title || `情報源 ${index + 1}`),
          url: String(source.url || source.link || "#"),
          publishedAt: validDate(source.publishedAt || source.published_at || source.date) || ""
        };
      })
      .filter(Boolean);
  }

  function normalizeParagraphs(value) {
    if (Array.isArray(value)) return value.map(String).filter(Boolean);
    if (typeof value === "string") return value.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
    return [];
  }

  function normalizeSections(value) {
    if (!Array.isArray(value)) return [];
    return value.map((section) => {
      if (typeof section === "string") return { heading: "", paragraphs: [section] };
      return {
        heading: String(section?.heading || section?.title || ""),
        paragraphs: normalizeParagraphs(section?.paragraphs || section?.body || section?.content || [])
      };
    }).filter((section) => section.heading || section.paragraphs.length);
  }

  function normalizeArchive(raw) {
    const entries = Array.isArray(raw) ? raw : raw?.editions;
    if (!Array.isArray(entries)) return [];
    return entries
      .map((item) => ({
        id: String(item.id || item.editionId || ""),
        label: String(item.label || item.title || "ニュースダイジェスト"),
        generatedAt: validDate(item.generatedAt || item.updatedAt) || "",
        articleCount: Number.isFinite(Number(item.articleCount)) ? Number(item.articleCount) : null,
        dataUrl: item.dataUrl || item.path ? String(item.dataUrl || item.path) : ""
      }))
      .filter((item) => item.id && item.generatedAt)
      .sort((a, b) => dateValue(b.generatedAt) - dateValue(a.generatedAt));
  }

  function editionToArchiveItem(data) {
    return {
      id: data.edition.id,
      label: data.edition.label,
      generatedAt: data.generatedAt,
      articleCount: data.articles.length,
      dataUrl: "data/latest.json"
    };
  }

  function deduplicate(articles) {
    const seen = new Set();
    return articles.filter((article) => {
      const key = article.id || article.title.toLocaleLowerCase("ja").replace(/\s+/g, "");
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function normalizeCategory(value) {
    const category = String(value).trim().toLowerCase();
    if (CATEGORY_META[category]) return category;
    return CATEGORY_ALIASES[category] || "other";
  }

  function sortedByImportance(articles) {
    return [...articles].sort((a, b) => b.importance - a.importance || dateValue(b.updatedAt) - dateValue(a.updatedAt));
  }

  async function fetchJson(path) {
    const url = new URL(path, document.baseURI);
    const response = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function formatCoverage(coverage) {
    if (!coverage?.start || !coverage?.end) return "対象期間：直近のニュース";
    const start = formatDate(coverage.start, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const end = formatDate(coverage.end, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
    return `対象 ${start}–${end}`;
  }

  function formatDate(value, options) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("ja-JP", { timeZone: TIMEZONE, ...options }).format(date);
  }

  function formatTime(value) {
    return formatDate(value, { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  function inferEditionLabel(value) {
    const hour = Number(formatDate(value, { hour: "2-digit", hour12: false }));
    if (hour < 9) return "朝刊";
    if (hour < 15) return "昼刊";
    return "夕刊";
  }

  function setTime(node, value, label) {
    node.dateTime = value || "";
    node.textContent = label;
  }

  function categoryLabel(category) {
    return CATEGORY_META[category]?.label || CATEGORY_META.other.label;
  }

  function queryUrl(params) {
    const url = new URL(window.location.href);
    url.search = "";
    for (const [key, value] of Object.entries(params)) {
      if (value) url.searchParams.set(key, value);
    }
    return `${url.pathname}${url.search}`;
  }

  function updateDocumentMeta(article) {
    const title = article ? `${article.title} | NEWSROOM 18` : `${state.data.edition.label} | NEWSROOM 18`;
    const description = article?.summary || article?.dek || state.data.summary || "1日3回のニュースダイジェスト";
    document.title = title;
    document.querySelector('meta[name="description"]')?.setAttribute("content", description);
    document.querySelector('meta[property="og:title"]')?.setAttribute("content", title);
    document.querySelector('meta[property="og:description"]')?.setAttribute("content", description);
  }

  async function copyCurrentUrl() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      dom.copyStatus.textContent = "コピーしました";
    } catch (_error) {
      const input = document.createElement("input");
      input.value = window.location.href;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.append(input);
      input.select();
      const copied = document.execCommand("copy");
      input.remove();
      dom.copyStatus.textContent = copied ? "コピーしました" : "コピーできませんでした";
    }
  }

  function safeExternalUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
    } catch (_error) {
      return "#";
    }
  }

  function showError(message, persistent = true) {
    dom.errorMessage.textContent = message;
    dom.errorState.hidden = false;
    dom.retryButton.hidden = !persistent;
  }

  function hideError() {
    dom.errorState.hidden = true;
  }

  function element(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function clear(node) {
    node.replaceChildren();
  }

  function toCamel(value) {
    return value.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
  }

  function slugify(value) {
    return value
      .normalize("NFKD")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 80);
  }

  function validDate(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : String(value);
  }

  function dateValue(value) {
    const time = new Date(value).getTime();
    return Number.isNaN(time) ? 0 : time;
  }

  function clamp(value, min, max) {
    const safeValue = Number.isFinite(value) ? value : min;
    return Math.min(Math.max(safeValue, min), max);
  }
})();
