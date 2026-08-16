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

  const EMPTY_EDITION = {
    schemaVersion: 1,
    site: {
      name: "NEWSROOM 18",
      timezone: TIMEZONE,
      description: "朝6時・昼12時・夕方18時に更新するニュースダイジェスト"
    },
    generatedAt: new Date().toISOString(),
    summary: "最新号を取得できませんでした。再読み込みして配信状況をご確認ください。",
    edition: { id: "unavailable", label: "最新号", slot: "" },
    coverage: { start: "", end: "" },
    articles: []
  };

  const state = {
    data: null,
    latestData: null,
    archive: [],
    recentFeatures: [],
    recentFeaturesForEditionId: "",
    selectedCategory: "all",
    searchQuery: "",
    loadFailed: false
  };

  const dom = {};

  document.addEventListener("DOMContentLoaded", initialize);

  async function initialize() {
    cacheDom();
    dom.retryButton.addEventListener("click", () => window.location.reload());
    dom.copyLinkButton.addEventListener("click", copyCurrentUrl);
    dom.searchInput.addEventListener("input", handleSearch);
    dom.searchClear.addEventListener("click", clearSearch);
    window.addEventListener("popstate", route);

    const [latestResult, archiveResult] = await Promise.allSettled([
      fetchJson("data/latest.json"),
      fetchJson("data/archive.json")
    ]);

    let rawData;
    if (latestResult.status === "fulfilled") {
      rawData = latestResult.value;
    } else {
      rawData = EMPTY_EDITION;
      state.loadFailed = true;
    }

    try {
      state.data = normalizeEdition(rawData);
    } catch (error) {
      console.error("Invalid news data:", error);
      state.data = normalizeEdition(EMPTY_EDITION);
      state.loadFailed = true;
    }

    state.archive = archiveResult.status === "fulfilled"
      ? normalizeArchive(archiveResult.value)
      : [];
    if (!state.archive.length) {
      state.archive = [editionToArchiveItem(state.data)];
    }

    state.latestData = state.data;
    if (!state.loadFailed && featureArticles(state.data.articles).length < 3) {
      state.recentFeatures = await loadRecentFeatures(state.data, state.archive, 3);
      state.recentFeaturesForEditionId = state.data.edition.id;
    }

    dom.loadingState.hidden = true;
    await route();
    if (state.loadFailed) {
      showError("最新号を取得できませんでした。通信状況を確認して、もう一度読み込んでください。", true);
    }
  }

  function cacheDom() {
    const ids = [
      "loading-state", "error-state", "error-message", "retry-button",
      "dashboard-view", "article-view", "edition-date", "edition-label", "edition-coverage",
      "last-updated", "brief-summary", "lead-kicker", "lead-heading", "lead-article", "lead-secondary-grid", "bulletin-list",
      "search-input", "search-clear", "category-filters", "edition-stats", "source-summary", "source-network",
      "important-grid", "news-sections", "result-count", "empty-state", "shorts-section", "shorts-list", "archive-list",
      "article-breadcrumb", "article-category", "article-status", "article-importance", "article-title",
      "article-dek", "article-updated", "article-fact-sheet", "article-verification",
      "article-facts-group", "article-facts-list", "article-impact-group", "article-impact-list",
      "article-background-group", "article-background",
      "article-watch-group", "article-watch-list", "article-body", "article-sources-list",
      "article-updates", "article-updates-list",
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

    if (!editionId && state.latestData && state.data.edition.id !== state.latestData.edition.id) {
      state.data = state.latestData;
    }

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

    renderDashboard(params.get("category") || "all", params.get("q") || "");
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

  async function loadRecentFeatures(currentData, archiveItems, visibleLimit = 3) {
    const currentTime = dateValue(currentData.generatedAt);
    const currentFeatures = sortedByImportance(featureArticles(currentData.articles));
    const missing = Math.max(0, visibleLimit - currentFeatures.length);
    if (!missing) return [];
    const candidates = archiveItems
      .filter((item) => item.id !== currentData.edition.id && item.dataUrl)
      .filter((item) => !currentTime || dateValue(item.generatedAt) < currentTime)
      .filter((item) => !currentTime || currentTime - dateValue(item.generatedAt) <= 48 * 60 * 60 * 1000)
      .slice(0, 8);

    const results = await Promise.allSettled(
      candidates.map(async (item) => normalizeEdition(await fetchJson(item.dataUrl)))
    );
    const editions = results.map((result) => result.status === "fulfilled" ? result.value : null);
    return selectRecentFeatures(editions, currentFeatures, missing);
  }

  function selectRecentFeatures(editions, currentFeatures = [], limit = 3) {
    const selected = [];
    const seen = new Set(currentFeatures.map((article) => paragraphKey(article.title)));
    for (const edition of editions) {
      if (!edition) continue;
      const features = sortedByImportance(featureArticles(edition.articles));
      for (const article of features) {
        const key = paragraphKey(article.title) || articleKey(article);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        selected.push(article);
        if (selected.length >= limit) return selected;
      }
    }
    return selected;
  }

  function dashboardRecentFeatures() {
    if (state.data.edition.id !== state.recentFeaturesForEditionId) return [];
    return state.recentFeatures;
  }

  function renderDashboard(requestedCategory = "all", requestedSearch = state.searchQuery) {
    dom.articleView.hidden = true;
    dom.dashboardView.hidden = false;

    const available = new Set([
      ...state.data.articles.map((article) => article.category),
      ...dashboardRecentFeatures().map((article) => article.category)
    ]);
    const validFilters = new Set(["all", "important", ...available]);
    state.selectedCategory = validFilters.has(requestedCategory) ? requestedCategory : "all";
    state.searchQuery = String(requestedSearch || "").trim();
    dom.searchInput.value = state.searchQuery;
    dom.searchClear.hidden = !state.searchQuery;

    renderLead();
    renderLeadSecondary();
    renderBulletin();
    renderFilters(available);
    renderImportant();
    renderNewsSections();
    renderEditionStats();
    renderSourceNetwork();
    updateGlobalNavigation();
    updateDocumentMeta();
  }

  function renderChrome(data) {
    dom.editionDate.textContent = formatDate(data.generatedAt, { year: "numeric", month: "long", day: "numeric", weekday: "short" });
    dom.editionLabel.textContent = data.edition.label;
    dom.editionCoverage.textContent = formatCoverage(data.coverage);
    setTime(dom.lastUpdated, data.generatedAt, formatDate(data.generatedAt, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }));
    dom.briefSummary.textContent = data.summary || `${data.articles.length}本のニュースを掲載しています。`;
  }

  function renderLead() {
    clear(dom.leadArticle);
    const currentFeatures = sortedByImportance(featureArticles(state.data.articles));
    const recentFeatures = dashboardRecentFeatures();
    const usingRecent = !currentFeatures.length && recentFeatures.length > 0;
    const article = currentFeatures[0] || recentFeatures[0] || sortedByImportance(state.data.articles)[0];

    dom.leadKicker.textContent = usingRecent ? "RECENT FEATURE / 前号から" : "TOP STORY / 総合";
    dom.leadHeading.textContent = usingRecent ? "直近の詳報" : "今日の一面";
    if (!article) {
      dom.leadArticle.append(element("p", "lead-article__empty", "最新ニュースはニュースラインに掲載しています。"));
      return;
    }

    if (usingRecent) {
      dom.leadArticle.append(element("p", "lead-article__origin", editionOriginLabel(article)));
    }
    dom.leadArticle.append(element("span", "lead-article__category", categoryLabel(article.category)));
    const heading = element("h2");
    heading.append(newsDestinationLink(article, article.title));
    dom.leadArticle.append(heading);
    if (article.dek || article.summary) {
      dom.leadArticle.append(element("p", "lead-article__dek", article.dek || article.summary));
    }
    dom.leadArticle.append(articleMeta(article, "lead-article__meta"));
  }

  function renderLeadSecondary() {
    clear(dom.leadSecondaryGrid);
    const lead = sortedByImportance(featureArticles(state.data.articles))[0]
      || dashboardRecentFeatures()[0]
      || sortedByImportance(state.data.articles)[0];
    const candidates = uniqueArticles([
      ...sortedByImportance(featureArticles(state.data.articles)),
      ...dashboardRecentFeatures(),
      ...sortedByImportance(state.data.articles)
    ]).filter((article) => articleKey(article) !== articleKey(lead)).slice(0, 3);

    if (!candidates.length) {
      dom.leadSecondaryGrid.append(element("p", "lead-secondary__empty", "最新記事はニュースラインに掲載しています。"));
      return;
    }

    candidates.forEach((article, index) => {
      const card = element("article", `lead-secondary lead-secondary--${index + 1}`);
      const top = element("div", "lead-secondary__top");
      top.append(
        element("span", "lead-secondary__category", categoryLabel(article.category)),
        element("span", "lead-secondary__type", article.articleType === "feature" ? "詳報" : "速報")
      );
      const heading = element("h3");
      heading.append(newsDestinationLink(article, article.title));
      card.append(top, heading);
      const summary = article.articleType === "brief"
        ? sanitizeBriefText(article.summary || article.dek)
        : article.summary || article.dek;
      if (summary) card.append(element("p", "lead-secondary__summary", summary));
      card.append(articleMeta(article, "story-meta"));
      dom.leadSecondaryGrid.append(card);
    });
  }

  function renderBulletin() {
    clear(dom.bulletinList);
    const leadId = sortedByImportance(featureArticles(state.data.articles))[0]?.id;
    const articles = [...state.data.articles]
      .filter((article) => article.id !== leadId)
      .sort((a, b) => dateValue(b.updatedAt) - dateValue(a.updatedAt))
      .slice(0, 8);

    for (const article of articles) {
      const item = document.createElement("li");
      const link = newsDestinationLink(article, article.title);
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

  function handleSearch(event) {
    state.searchQuery = String(event.target.value || "").trim();
    dom.searchClear.hidden = !state.searchQuery;
    const url = new URL(window.location.href);
    url.searchParams.delete("article");
    url.searchParams.delete("edition");
    if (state.searchQuery) url.searchParams.set("q", state.searchQuery);
    else url.searchParams.delete("q");
    window.history.replaceState({}, "", url);
    renderImportant();
    renderNewsSections();
  }

  function clearSearch() {
    dom.searchInput.value = "";
    dom.searchInput.focus();
    handleSearch({ target: dom.searchInput });
  }

  function selectCategory(category) {
    state.selectedCategory = category;
    const url = new URL(window.location.href);
    url.searchParams.delete("article");
    url.searchParams.delete("edition");
    if (category === "all") url.searchParams.delete("category");
    else url.searchParams.set("category", category);
    if (state.searchQuery) url.searchParams.set("q", state.searchQuery);
    window.history.replaceState({}, "", url);
    renderFilters(new Set(state.data.articles.map((article) => article.category)));
    renderImportant();
    renderNewsSections();
    updateGlobalNavigation();
    document.getElementById("all-news-heading")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderImportant() {
    clear(dom.importantGrid);
    let articles = uniqueArticles([
      ...sortedByImportance(featureArticles(state.data.articles)),
      ...sortedByImportance(dashboardRecentFeatures())
    ]);
    articles = articles.filter(matchesSearch);
    if (state.selectedCategory === "important") {
      articles = articles.filter((article) => article.importance >= 4);
    } else if (state.selectedCategory !== "all") {
      articles = articles.filter((article) => article.category === state.selectedCategory);
    }
    articles.slice(0, 3).forEach((article, index) => dom.importantGrid.append(storyCard(article, {
      important: true,
      prominent: index === 0
    })));
    dom.importantGrid.parentElement.hidden = !articles.length;
  }

  function renderNewsSections() {
    clear(dom.newsSections);
    let articles = state.data.articles.filter(matchesSearch);
    if (state.selectedCategory === "important") {
      articles = articles.filter((article) => article.importance >= 4);
    } else if (state.selectedCategory !== "all") {
      articles = articles.filter((article) => article.category === state.selectedCategory);
    }

    const features = featureArticles(articles);
    const briefs = briefArticles(articles);
    let recentFeatures = dashboardRecentFeatures();
    recentFeatures = recentFeatures.filter(matchesSearch);
    if (state.selectedCategory === "important") {
      recentFeatures = recentFeatures.filter((article) => article.importance >= 4);
    } else if (state.selectedCategory !== "all") {
      recentFeatures = recentFeatures.filter((article) => article.category === state.selectedCategory);
    }
    const currentCount = articles.length;
    const recentCount = recentFeatures.length ? `、直近の詳報 ${recentFeatures.length}本を併載` : "";
    dom.resultCount.textContent = state.searchQuery
      ? `「${state.searchQuery}」に一致：${currentCount + recentFeatures.length}項目`
      : `ニュース項目 ${currentCount}件（詳報 ${features.length}件・速報 ${briefs.length}件）${recentCount}`;
    dom.emptyState.hidden = Boolean(articles.length || recentFeatures.length);

    renderShorts(briefs);

    if (recentFeatures.length) {
      const section = element("section", "desk-section desk-section--recent");
      section.setAttribute("aria-labelledby", "category-recent-features");
      const heading = element("div", "desk-section__heading");
      const title = element("h3", "", "特集・解説");
      title.id = "category-recent-features";
      const label = element("span", "", `${editionOriginLabel(recentFeatures[0])}から`);
      heading.append(title, label);
      const grid = element("div", "feature-shelf");
      recentFeatures.forEach((article, index) => grid.append(storyCard(article, { prominent: index === 0 })));
      section.append(heading, grid);
      dom.newsSections.append(section);
    }

    for (const category of CATEGORY_ORDER) {
      const categoryArticles = articles
        .filter((article) => article.category === category)
        .sort((a, b) => {
          const featureDifference = Number(b.articleType === "feature") - Number(a.articleType === "feature");
          return featureDifference || b.importance - a.importance || dateValue(b.updatedAt) - dateValue(a.updatedAt);
        });
      if (!categoryArticles.length) continue;

      const section = element("section", "desk-section");
      section.setAttribute("aria-labelledby", `category-${category}`);
      const heading = element("div", "desk-section__heading");
      const title = element("h3", "", categoryLabel(category));
      title.id = `category-${category}`;
      heading.append(
        title,
        element("span", "", `${CATEGORY_META[category]?.english || "NEWS"} / ${categoryArticles.length}項目`)
      );

      const layout = element("div", "desk-layout");
      layout.append(storyCard(categoryArticles[0], { deskLead: true }));
      if (categoryArticles.length > 1) {
        const list = element("div", "desk-list");
        categoryArticles.slice(1, 6).forEach((article) => list.append(storyCard(article, { compact: true })));
        layout.append(list);
      }
      section.append(heading, layout);
      dom.newsSections.append(section);
    }
  }

  function renderShorts(articles) {
    clear(dom.shortsList);
    dom.shortsSection.hidden = !articles.length;

    const sorted = [...articles].sort((a, b) => dateValue(b.updatedAt) - dateValue(a.updatedAt));
    for (const article of sorted) {
      const item = element("li", "shorts-item");
      const meta = element("div", "shorts-item__meta");
      meta.append(
        element("span", "shorts-item__category", categoryLabel(article.category)),
        element("time", "", `${formatTime(article.updatedAt)} 更新`)
      );
      meta.querySelector("time").dateTime = article.updatedAt;

      const title = element("h3", "shorts-item__title");
      title.append(newsDestinationLink(article, article.title));
      const sourceList = element("div", "shorts-item__sources");
      sourceList.append(element("span", "", "原典"));
      const linkedSources = article.sources
        .map((source) => ({ source, url: firstSourceUrl(source) }))
        .filter((item) => item.url !== "#");
      if (linkedSources.length) {
        linkedSources.forEach(({ source, url }, index) => {
          if (index) sourceList.append(document.createTextNode(" / "));
          const link = document.createElement("a");
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer nofollow";
          link.textContent = source.name;
          sourceList.append(link);
        });
      } else {
        sourceList.append(document.createTextNode(" 出典リンク未掲載"));
      }
      item.append(meta, title);
      const summary = sanitizeBriefText(article.summary || article.dek);
      if (summary) item.append(element("p", "shorts-item__summary", summary));
      item.append(sourceList);
      dom.shortsList.append(item);
    }
  }

  function storyCard(article, options = {}) {
    if (options === true) options = { important: true };
    const classes = ["story-card"];
    if (options.important) classes.push("story-card--important");
    if (options.prominent) classes.push("story-card--prominent");
    if (options.deskLead) classes.push("story-card--desk-lead");
    if (options.compact) classes.push("story-card--compact");
    if (article.articleType === "brief") classes.push("story-card--live");
    const card = element("article", classes.join(" "));
    const top = element("div", "story-card__top");
    const category = element("span", "story-card__category", categoryLabel(article.category));
    const format = element("span", "story-card__format", article.articleType === "feature" ? "詳報" : "速報");
    top.append(category, format);
    const title = element("h3");
    title.append(newsDestinationLink(article, article.title));
    const summaryText = article.articleType === "brief"
      ? sanitizeBriefText(article.summary || article.dek)
      : article.summary || article.dek;
    const summary = element("p", "story-card__summary", summaryText);
    const meta = articleMeta(article, "story-meta");
    if (article.editionId && article.editionId !== state.data.edition.id) {
      card.append(element("p", "story-card__origin", editionOriginLabel(article)));
    }
    card.append(top, title);
    if (summaryText && !options.compact) card.append(summary);
    card.append(meta, sourceLinks(article, options.compact ? 2 : 3));
    return card;
  }

  function sourceLinks(article, limit = 3) {
    const container = element("p", "story-card__sources");
    container.append(document.createTextNode("配信元 "));
    const sources = article.sources.slice(0, limit);
    if (!sources.length) {
      container.append(document.createTextNode("未掲載"));
      return container;
    }
    sources.forEach((source, index) => {
      if (index) container.append(document.createTextNode(" / "));
      const safeUrl = safeExternalUrl(source.url);
      if (safeUrl === "#") {
        container.append(document.createTextNode(source.name));
      } else {
        const link = document.createElement("a");
        link.href = safeUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer nofollow";
        link.textContent = source.name;
        container.append(link);
      }
    });
    if (article.sources.length > limit) {
      container.append(document.createTextNode(` ほか${article.sources.length - limit}件`));
    }
    return container;
  }

  function renderEditionStats() {
    clear(dom.editionStats);
    const articles = state.data.articles;
    const featureCount = featureArticles(articles).length;
    const publisherCount = new Set(
      articles.flatMap((article) => article.sources.map((source) => source.publisherId || paragraphKey(source.name)))
    ).size;
    const categoryCount = new Set(articles.map((article) => article.category)).size;
    const originals = uniqueEditionOriginals(articles);
    const stats = [
      ["ニュース項目", `${articles.length}件`],
      ["現号の詳報", `${featureCount}本`],
      ["直近の詳報", `${dashboardRecentFeatures().length}本`],
      ["速報・短報", `${briefArticles(articles).length}件`],
      ["原典", `${originals.length}件`],
      ["配信元", `${publisherCount}件`],
      ["ニュース面", `${categoryCount}分野`],
      ["継続資料", `${originals.filter((item) => item.isContinuation).length}件`]
    ];
    stats.forEach(([label, value]) => {
      const group = element("div", "edition-stats__item");
      group.append(element("dt", "", label), element("dd", "", value));
      dom.editionStats.append(group);
    });
  }

  function renderSourceNetwork() {
    clear(dom.sourceNetwork);
    const publishers = new Map();
    state.data.articles.forEach((article) => {
      article.sources.forEach((source) => {
        const key = source.publisherId || paragraphKey(source.name);
        if (!key) return;
        const record = publishers.get(key) || {
          name: source.name,
          url: "#",
          count: 0,
          primaryCount: 0
        };
        record.count += 1;
        record.primaryCount += Number(source.isPrimary);
        const url = safeExternalUrl(source.url);
        if (record.url === "#" && url !== "#") record.url = url;
        publishers.set(key, record);
      });
    });

    const sorted = [...publishers.values()].sort((a, b) =>
      b.primaryCount - a.primaryCount || b.count - a.count || a.name.localeCompare(b.name, "ja")
    );
    const primaryCount = sorted.filter((publisher) => publisher.primaryCount > 0).length;
    const originalCount = uniqueEditionOriginals(state.data.articles).length;
    dom.sourceSummary.textContent = sorted.length
      ? `${sorted.length}件の配信元・機関、原典 ${originalCount}件を参照${primaryCount ? `（一次情報 ${primaryCount}件）` : ""}`
      : "配信元情報を確認しています。";

    sorted.slice(0, 10).forEach((publisher) => {
      const item = document.createElement("li");
      const name = publisher.url === "#" ? element("span", "", publisher.name) : document.createElement("a");
      if (name instanceof HTMLAnchorElement) {
        name.href = publisher.url;
        name.target = "_blank";
        name.rel = "noopener noreferrer nofollow";
        name.textContent = publisher.name;
      }
      const meta = element("span", "", `${publisher.count}記事${publisher.primaryCount ? "・一次情報" : ""}`);
      item.append(name, meta);
      dom.sourceNetwork.append(item);
    });
  }

  function uniqueEditionOriginals(articles) {
    const originals = new Map();
    articles.forEach((article) => {
      article.sources.forEach((source) => {
        const links = source.links.length ? source.links : [{
          title: source.name,
          url: source.url,
          isContinuation: false
        }];
        links.forEach((item) => {
          const url = safeExternalUrl(item.url);
          if (url === "#") return;
          const existing = originals.get(url);
          originals.set(url, existing ? {
            ...existing,
            isContinuation: existing.isContinuation || item.isContinuation
          } : item);
        });
      });
    });
    return [...originals.values()];
  }

  function updateGlobalNavigation() {
    const links = document.querySelectorAll(".global-nav a");
    const available = new Set([
      ...state.data.articles.map((article) => article.category),
      ...dashboardRecentFeatures().map((article) => article.category)
    ]);
    links.forEach((link) => {
      const linkUrl = new URL(link.href, window.location.href);
      const linkCategory = linkUrl.searchParams.get("category") || "all";
      const isSectionLink = linkCategory !== "all" && !link.hash;
      link.hidden = isSectionLink && !available.has(linkCategory);
      const isCurrent = !link.hash && linkCategory === state.selectedCategory;
      if (isCurrent) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }

  function matchesSearch(article) {
    if (!state.searchQuery) return true;
    const searchable = [
      article.title,
      article.dek,
      article.summary,
      categoryLabel(article.category),
      ...article.facts,
      ...article.impactPoints,
      ...article.background,
      ...article.watchPoints,
      ...article.body,
      ...article.tags,
      ...article.sources.flatMap((source) => [
        source.name,
        ...source.keyPoints,
        ...source.links.map((link) => link.title)
      ])
    ].join(" ");
    const haystack = comparisonText(searchable);
    return state.searchQuery
      .split(/\s+/)
      .map(comparisonText)
      .filter(Boolean)
      .every((term) => haystack.includes(term));
  }

  function uniqueArticles(articles) {
    const seen = new Set();
    return articles.filter((article) => {
      const key = paragraphKey(article.title) || articleKey(article);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function articleKey(article) {
    if (!article) return "";
    return `${article.editionId || ""}:${article.id || article.slug || paragraphKey(article.title)}`;
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
      const count = element("span", "", `${item.articleCount ?? "—"}項目・${formatTime(item.generatedAt)}更新`);
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
    dom.articleStatus.textContent = article.continuationSourceCount > 0
      ? `新着 ${article.freshSourceCount}・継続 ${article.continuationSourceCount}`
      : `新着 ${article.freshSourceCount}`;
    dom.articleImportance.textContent = article.importance >= 4 ? `重要度 ${article.importance}/5` : "";
    dom.articleTitle.textContent = article.title;
    dom.articleDek.textContent = article.dek || article.summary;
    setTime(dom.articleUpdated, article.updatedAt, formatDate(article.updatedAt, {
      year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit"
    }));
    dom.copyStatus.textContent = "";

    renderFactSheet(article);
    renderArticleBody(article);
    renderSources(article);
    renderUpdates(article);
    renderRelated(article);
    updateDocumentMeta(article);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function renderFactSheet(article) {
    clear(dom.articleFactsList);
    clear(dom.articleImpactList);
    clear(dom.articleBackground);
    clear(dom.articleWatchList);

    article.facts.forEach((fact) => dom.articleFactsList.append(element("li", "", fact)));
    article.impactPoints.forEach((point) => dom.articleImpactList.append(element("li", "", point)));
    article.background.forEach((paragraph) => dom.articleBackground.append(element("p", "", paragraph)));
    article.watchPoints.forEach((point) => dom.articleWatchList.append(element("li", "", point)));

    dom.articleFactsGroup.hidden = !article.facts.length;
    dom.articleImpactGroup.hidden = !article.impactPoints.length;
    dom.articleBackgroundGroup.hidden = !article.background.length;
    dom.articleWatchGroup.hidden = !article.watchPoints.length;

    const secondaryGroups = [
      { node: dom.articleImpactGroup, visible: article.impactPoints.length > 0 },
      { node: dom.articleBackgroundGroup, visible: article.background.length > 0 },
      { node: dom.articleWatchGroup, visible: article.watchPoints.length > 0 }
    ].filter((group) => group.visible);
    secondaryGroups.forEach((group, index) => {
      const isLastOdd = secondaryGroups.length % 2 === 1 && index === secondaryGroups.length - 1;
      group.node.classList.toggle("fact-group--wide", isLastOdd);
      group.node.classList.toggle("fact-group--split-left", !isLastOdd && index % 2 === 0);
    });

    const primaryCount = article.sources.filter((source) => source.isPrimary).length;
    if (article.sources.length) {
      dom.articleVerification.textContent = primaryCount
        ? `原典 ${article.sourceCount}件・配信元 ${article.publisherCount}件（一次情報 ${primaryCount}件）`
        : `原典 ${article.sourceCount}件・配信元 ${article.publisherCount}件`;
    } else {
      dom.articleVerification.textContent = "出典リンク未掲載";
    }

    dom.articleFactSheet.hidden = !(
      article.facts.length || article.impactPoints.length || article.background.length || article.watchPoints.length
    );
  }

  function renderArticleBody(article) {
    clear(dom.articleBody);
    dom.articleBody.hidden = false;

    const detailedSections = article.sections.filter((section) => !isOverviewSection(section.heading));
    if (detailedSections.length) {
      detailedSections.forEach((section) => {
        if (section.heading) dom.articleBody.append(element("h2", "", section.heading));
        section.paragraphs.forEach((paragraph) => dom.articleBody.append(element("p", "", paragraph)));
      });
      return;
    }

    const overviewParagraphs = new Set(
      [...article.facts, ...article.impactPoints, ...article.background, ...article.watchPoints].map(paragraphKey)
    );
    const overviewText = [...article.facts, ...article.impactPoints, ...article.background, ...article.watchPoints];
    const paragraphs = article.body
      .filter((paragraph) => !overviewParagraphs.has(paragraphKey(paragraph)))
      .map((paragraph) => distinctParagraphContent(paragraph, overviewText))
      .filter(Boolean);
    if (paragraphs.length) {
      dom.articleBody.append(element("h2", "", "詳しく"));
      paragraphs.forEach((paragraph) => dom.articleBody.append(element("p", "", paragraph)));
      return;
    }

    dom.articleBody.hidden = true;
  }

  function renderSources(article) {
    const sources = article.sources;
    clear(dom.articleSourcesList);
    if (!sources.length) {
      dom.articleSourcesList.append(element("li", "", "この記事に出典リンクは掲載されていません。"));
      return;
    }
    for (const source of sources) {
      const li = element("li", "source-card");
      const header = element("div", "source-card__header");
      const heading = document.createElement("h3");
      heading.textContent = source.name;
      header.append(heading);

      const metadata = element("div", "source-card__meta");
      if (source.isPrimary) metadata.append(element("span", "source-badge", "一次情報"));
      else if (source.type) metadata.append(element("span", "source-badge", source.type));
      if (source.publishedAt) {
        const time = document.createElement("time");
        time.dateTime = source.publishedAt;
        time.textContent = formatDate(source.publishedAt, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
        metadata.append(time);
      }
      header.append(metadata);
      li.append(header);

      const points = uniqueParagraphs(source.keyPoints).filter(
        (point) => !isProceduralKeyPoint(point)
      );
      if (points.length) {
        const pointLabel = element("p", "source-card__label", "この記事に反映した要点");
        const pointList = element("ul", "source-card__points");
        points.forEach((point) => pointList.append(element("li", "", point)));
        li.append(pointLabel, pointList);
      }
      if (source.links.length) {
        const linkLabel = element(
          "p",
          "source-card__label",
          source.links.length > 1 ? `参照した原典 ${source.links.length}件` : "参照した原典"
        );
        const linkList = element("ul", "source-card__links");
        source.links.forEach((item) => {
          const row = document.createElement("li");
          const anchor = document.createElement("a");
          anchor.href = safeExternalUrl(item.url);
          anchor.target = "_blank";
          anchor.rel = "noopener noreferrer nofollow";
          anchor.textContent = item.title || source.name;
          row.append(anchor);
          const details = [item.isContinuation ? "継続" : "新着"];
          if (item.publishedAt) {
            details.push(formatDate(item.publishedAt, {
              month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"
            }));
          }
          if (item.originEditionId) details.push(`元版 ${item.originEditionId}`);
          if (details.length) row.append(element("span", "source-card__link-meta", details.join("・")));
          linkList.append(row);
        });
        li.append(linkLabel, linkList);
      }
      if (source.isPrimary) {
        li.append(element(
          "p",
          "source-card__attribution",
          `${source.name}の公開情報をもとにNEWSROOM 18が要約・加工`
        ));
      }
      dom.articleSourcesList.append(li);
    }
  }

  function renderUpdates(article) {
    clear(dom.articleUpdatesList);
    const updates = buildUpdateTimeline(article);
    dom.articleUpdates.hidden = !updates.length;

    for (const update of updates) {
      const item = document.createElement("li");
      const marker = element("span", "article-updates__marker");
      const content = element("div", "article-updates__content");
      if (update.at) {
        const time = document.createElement("time");
        time.dateTime = update.at;
        time.textContent = formatDate(update.at, {
          year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"
        });
        content.append(time);
      }
      content.append(element("p", "", update.text));
      if (update.source) content.append(element("span", "", update.source));
      item.append(marker, content);
      dom.articleUpdatesList.append(item);
    }
  }

  function buildUpdateTimeline(article) {
    const seen = new Set();
    return article.updates
      .filter(isMaterialUpdate)
      .filter((update) => {
        const key = `${update.at}|${update.text}|${update.source}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((a, b) => dateValue(b.at) - dateValue(a.at));
  }

  function renderRelated(article) {
    clear(dom.relatedGrid);
    const related = featureArticles(state.data.articles)
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
    const sources = element(
      "span",
      "source-count",
      `${article.sourceCount}原典・${article.publisherCount}配信元`
    );
    meta.append(time, sources);
    const fromAnotherEdition = article.editionId && article.editionId !== state.data.edition.id;
    if (!fromAnotherEdition && (article.freshSourceCount > 0 || article.continuationSourceCount > 0)) {
      meta.append(element(
        "span",
        "continuation-count",
        article.continuationSourceCount > 0
          ? `新着${article.freshSourceCount}・継続${article.continuationSourceCount}`
          : `新着${article.freshSourceCount}`
      ));
    }
    return meta;
  }

  function headingLink(article, tagName) {
    const heading = document.createElement(tagName);
    heading.append(articleLink(article, article.title));
    return heading;
  }

  function articleLink(article, text) {
    const link = document.createElement("a");
    const params = {};
    if (article.editionId) {
      params.edition = article.editionId;
    }
    params.article = article.slug || article.id;
    link.href = queryUrl(params);
    link.textContent = text;
    return link;
  }

  function newsDestinationLink(article, text) {
    if (article.articleType !== "brief") return articleLink(article, text);
    const sourceUrl = article.sources.map(firstSourceUrl).find((url) => url !== "#");
    if (!sourceUrl) return element("span", "", text);
    const link = document.createElement("a");
    link.href = sourceUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer nofollow";
    link.textContent = text;
    return link;
  }

  function firstSourceUrl(source) {
    if (!source || typeof source !== "object") return "#";
    for (const item of Array.isArray(source.links) ? source.links : []) {
      const url = safeExternalUrl(item?.url);
      if (url !== "#") return url;
    }
    return safeExternalUrl(source.url);
  }

  function featureArticles(articles) {
    return articles.filter((article) => article.articleType !== "brief");
  }

  function briefArticles(articles) {
    return articles.filter((article) => article.articleType === "brief");
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
    const editionLabel = String(edition.label || edition.name || inferEditionLabel(generatedAt));
    const editionArticles = articles.map((article) => ({
      ...article,
      editionId: id,
      editionLabel,
      editionGeneratedAt: generatedAt
    }));

    return {
      schemaVersion: raw.schemaVersion || 1,
      site: raw.site || {},
      generatedAt,
      summary: String(raw.summary || raw.description || ""),
      generationMode: String(raw.generationMode || ""),
      edition: {
        id,
        label: editionLabel,
        slot: String(edition.slot || formatTime(generatedAt))
      },
      coverage: {
        start: validDate(raw.coverage?.start || raw.coverage?.from || raw.windowStart),
        end: validDate(raw.coverage?.end || raw.coverage?.to || raw.windowEnd)
      },
      articles: editionArticles
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
    let body = normalizeParagraphs(item.body || item.content || []);
    let sections = normalizeSections(item.sections || []);
    let facts = normalizeParagraphs(item.facts || item.confirmedFacts || item.keyFacts || []);
    if (!facts.length) facts = paragraphsFromSections(sections, /要点|確認できた事実|何が起きた|概要/);
    if (!facts.length && (item.summary || item.description)) facts = [String(item.summary || item.description)];

    let background = normalizeParagraphs(item.background || item.context || []);
    if (!background.length) background = paragraphsFromSections(sections, /背景|経緯|これまで/);

    let impactPoints = normalizeParagraphs(
      item.impactPoints || item.impact || item.impacts || item.affectedAreas || item.consequences || []
    );
    if (!impactPoints.length) {
      impactPoints = paragraphsFromSections(sections, /影響|対象地域|被害|注意点/);
    }

    let watchPoints = normalizeParagraphs(item.watchPoints || item.whatToWatch || item.outlook || []);
    watchPoints = uniqueParagraphs([
      ...watchPoints,
      ...normalizeParagraphs(item.whyItMatters || []),
      ...paragraphsFromSections(sections, /注目|なぜ重要|今後/)
    ]);
    facts = uniqueParagraphs(facts);
    impactPoints = uniqueParagraphs(impactPoints);
    background = uniqueParagraphs(background);
    const updates = normalizeUpdates(item.updates || item.updateHistory || item.revisions || []);
    const rawDek = String(item.dek || item.subtitle || item.lead || "");
    const rawSummary = String(item.summary || item.description || item.dek || "");

    body = normalizeEditorialParagraphs(body, title).filter((paragraph) => !isGenericWatchPoint(paragraph));
    sections = sections.map((section) => ({
      ...section,
      paragraphs: normalizeEditorialParagraphs(section.paragraphs, title).filter(
        (paragraph) => !isGenericWatchPoint(paragraph)
      )
    })).filter((section) => section.paragraphs.length);
    facts = normalizeEditorialParagraphs(facts, title);
    impactPoints = normalizeEditorialParagraphs(impactPoints, title);
    background = normalizeEditorialParagraphs(background, title);
    watchPoints = normalizeEditorialParagraphs(watchPoints, title).filter(
      (point) => !isGenericWatchPoint(point)
    );

    const articleType = normalizeArticleType(
      item.articleType || item.storyType || item.format || item.presentation,
      { facts, impactPoints, background, body, sections }
    );

    const cleanedDek = normalizeEditorialText(rawDek, title);
    const cleanedSummary = normalizeEditorialText(rawSummary, title);
    const finalDek = articleType === "brief" ? sanitizeBriefText(cleanedDek) : cleanedDek;
    const finalSummary = articleType === "brief" ? sanitizeBriefText(cleanedSummary) : cleanedSummary;
    if (articleType === "brief") {
      facts = facts.map(sanitizeBriefText).filter(Boolean);
    }
    const factLead = facts[0] || "";

    const sourceCount = positiveInteger(item.sourceCount)
      || new Set(sources.flatMap((source) => source.links.length
        ? source.links.map((link) => safeExternalUrl(link.url))
        : [safeExternalUrl(source.url)]).filter((url) => url !== "#")).size
      || sources.length;
    const publisherCount = positiveInteger(item.publisherCount) || sources.length;
    const continuationSourceCount = item.continuationSourceCount == null
      ? 0
      : nonNegativeInteger(item.continuationSourceCount);
    const freshSourceCount = item.freshSourceCount == null
      ? Math.max(0, sourceCount - continuationSourceCount)
      : nonNegativeInteger(item.freshSourceCount);

    return {
      id,
      slug,
      title,
      dek: finalDek || finalSummary || factLead,
      summary: finalSummary || finalDek || factLead,
      category: normalizeCategory(item.category || item.section || "other"),
      importance: clamp(Number(item.importance ?? item.priority ?? 3), 1, 5),
      articleType,
      publishedAt,
      updatedAt,
      sources,
      sourceCount,
      publisherCount,
      freshSourceCount,
      continuationSourceCount,
      continuationOrigins: Array.isArray(item.continuationOrigins)
        ? item.continuationOrigins.map(String).filter(Boolean)
        : [],
      body,
      sections,
      facts,
      impactPoints,
      background,
      watchPoints,
      updates,
      tags: Array.isArray(item.tags) ? item.tags.map(String) : []
    };
  }

  function normalizeSources(value) {
    const list = Array.isArray(value) ? value : [value];
    return list
      .map((source, index) => {
        if (typeof source === "string") {
          return {
            name: `情報源 ${index + 1}`,
            url: source,
            publishedAt: "",
            type: "",
            isPrimary: false,
            publisherId: "",
            keyPoints: [],
            links: [{
              title: `情報源 ${index + 1}`,
              url: source,
              publishedAt: "",
              isContinuation: false,
              originEditionId: ""
            }]
          };
        }
        if (!source || typeof source !== "object") return null;
        const normalizedUrl = String(source.url || source.link || "#");
        const links = normalizeSourceLinks(source.links || source.articles || source.items || []);
        return {
          name: String(source.name || source.publisher || source.title || `情報源 ${index + 1}`),
          publisherId: String(source.publisherId || source.publisher_id || ""),
          url: normalizedUrl,
          publishedAt: validDate(source.publishedAt || source.published_at || source.date) || "",
          type: String(source.type || source.kind || ""),
          isPrimary: Boolean(source.isPrimary || source.primary),
          keyPoints: uniqueParagraphs(normalizeParagraphs(
            source.keyPoints || source.points || source.summary || source.note || []
          )),
          links: links.length ? links : normalizedUrl === "#" ? [] : [{
            title: String(source.title || source.name || source.publisher || `情報源 ${index + 1}`),
            url: normalizedUrl,
            publishedAt: validDate(source.publishedAt || source.published_at || source.date) || "",
            isContinuation: Boolean(source.isContinuation || source.continuation),
            originEditionId: String(source.originEditionId || source.originEdition || "")
          }]
        };
      })
      .filter(Boolean);
  }

  function normalizeSourceLinks(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    return value.map((item) => {
      if (typeof item === "string") {
        return {
          title: "原典を開く",
          url: item,
          publishedAt: "",
          isContinuation: false,
          originEditionId: ""
        };
      }
      if (!item || typeof item !== "object") return null;
      return {
        title: String(item.title || item.headline || item.name || "原典を開く"),
        url: String(item.url || item.link || "#"),
        publishedAt: validDate(item.publishedAt || item.published_at || item.date) || "",
        isContinuation: Boolean(item.isContinuation || item.continuation),
        originEditionId: String(item.originEditionId || item.originEdition || "")
      };
    }).filter((item) => {
      if (!item || safeExternalUrl(item.url) === "#") return false;
      const key = safeExternalUrl(item.url);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function nonNegativeInteger(value) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? Math.floor(number) : 0;
  }

  function positiveInteger(value) {
    const number = nonNegativeInteger(value);
    return number > 0 ? number : 0;
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

  function normalizeUpdates(value) {
    const list = Array.isArray(value) ? value : value ? [value] : [];
    return list.map((update) => {
      if (typeof update === "string") {
        return { at: "", text: update, source: "", kind: "", material: null };
      }
      if (!update || typeof update !== "object") return null;
      const material = typeof update.material === "boolean"
        ? update.material
        : typeof update.isMaterial === "boolean"
          ? update.isMaterial
          : null;
      return {
        at: validDate(update.at || update.updatedAt || update.time || update.date) || "",
        text: String(update.text || update.note || update.summary || update.change || ""),
        source: String(update.source || update.by || ""),
        kind: String(update.kind || update.type || ""),
        material
      };
    }).filter((update) => update?.text);
  }

  function paragraphsFromSections(sections, headingPattern) {
    return uniqueParagraphs(
      sections
        .filter((section) => headingPattern.test(section.heading))
        .flatMap((section) => section.paragraphs)
    );
  }

  function isOverviewSection(heading) {
    return /要点|確認できた事実|何が起きた|概要|影響|対象地域|被害|注意点|背景|経緯|これまで|注目|なぜ重要|今後/.test(heading);
  }

  function normalizeArticleType(value, detail) {
    const type = String(value || "").trim().toLocaleLowerCase("ja");
    if (["brief", "short", "newsbrief", "news-brief", "bulletin", "速報", "短報"].includes(type)) {
      return "brief";
    }
    if (["feature", "full", "analysis", "longform", "long-form", "詳報", "解説"].includes(type)) {
      return "feature";
    }

    const detailedSections = detail.sections.filter((section) => !isOverviewSection(section.heading));
    const bodyLength = detail.body.join("").length;
    return detail.facts.length >= 3
      || detail.impactPoints.length > 0
      || detail.background.length > 0
      || detailedSections.length > 0
      || bodyLength >= 240
      ? "feature"
      : "brief";
  }

  function sanitizeBriefText(value) {
    const sentences = splitSentences(String(value || "").trim());
    return sentences.filter((sentence) => {
      const text = sentence.trim();
      if (!text) return false;
      const sourceDirection = /(?:詳しい|詳しくは|詳細|全文|続報|最新情報).{0,80}(?:出典|原典|リンク|配信元|公式サイト|元記事).{0,50}(?:確認|参照|ご覧)/.test(text)
        || /(?:出典|原典|リンク|配信元|公式サイト|元記事).{0,60}(?:確認|参照|ご覧)(?:ください|できます)?/.test(text);
      const headlineAttribution = /^.{1,80}(?:は|が)「.+」と(?:報じ|伝え)ました[。.]?$/.test(text)
        || /^.{1,100}(?:は|が)この動きを(?:報じ|伝え)ています[。.]?$/.test(text)
        || /^.{1,100}の配信概要では、/.test(text);
      return !(sourceDirection || headlineAttribution || isProceduralKeyPoint(text));
    }).join("");
  }

  function normalizeEditorialParagraphs(values, title) {
    return uniqueParagraphs(
      values.map((value) => normalizeEditorialText(value, title)).filter(Boolean)
    );
  }

  function normalizeEditorialText(value, title) {
    const titleKey = paragraphKey(title);
    return splitSentences(String(value || "").trim()).map((sentence) => {
      const text = sentence.trim();
      if (!text) return "";
      if (/^(?:一次情報を発信する|一次情報を提供する).{1,160}(?:更新|発表|配信)(?:です|となります)[。.]?$/.test(text)) {
        return "";
      }
      if (/^.{1,100}(?:は|が)この動きを(?:報じ|伝え)ています[。.]?$/.test(text)) {
        return "";
      }

      const attribution = text.match(/^.{1,80}?(?:は|が)[「『](.+)[」』]と(?:報じ|伝え)ました[。.]?$/);
      if (attribution && paragraphKey(attribution[1]) === titleKey) return "";

      return text.replace(/^.{1,100}?の配信概要では[、,]\s*/, "").trim();
    }).filter(Boolean).join("");
  }

  function isGenericWatchPoint(value) {
    const text = paragraphKey(value);
    return [
      /各配信元.*(?:続報|訂正)/,
      /別の独立した配信元.*(?:確認|報道)/,
      /関係機関や当事者.*公式発表/,
      /一次情報として扱える発表.*(?:解釈|影響).*別の独立した報道.*確認/,
      /(?:現時点では)?単一の配信元.*(?:続報|照合|訂正)/,
      /^(?:今後の)?公式発表や続報.*(?:注目|確認)/
    ].some((pattern) => pattern.test(text));
  }

  function uniqueParagraphs(value) {
    const seen = new Set();
    return value.map(String).map((paragraph) => paragraph.trim()).filter((paragraph) => {
      if (!paragraph) return false;
      const key = paragraphKey(paragraph);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function paragraphKey(value) {
    return String(value).normalize("NFKC").replace(/\s+/g, "").toLocaleLowerCase("ja");
  }

  function distinctParagraphContent(paragraph, references) {
    const value = String(paragraph).trim();
    if (!value || hasHighTextOverlap(value, references)) return "";

    const sentences = splitSentences(value);
    if (sentences.length <= 1) return value;
    const distinct = sentences.filter((sentence) => {
      if (isProceduralDetailSentence(sentence)) return false;
      const comparable = sentence.replace(/^.{1,100}?の配信概要では、/, "").trim();
      return !hasHighTextOverlap(comparable, references);
    });
    if (!distinct.length) return "";
    return distinct.length === sentences.length ? value : distinct.join("");
  }

  function hasHighTextOverlap(value, references) {
    const candidate = comparisonText(value);
    const comparableReferences = references.map(comparisonText).filter(Boolean);
    if (!candidate || !comparableReferences.length) return false;
    if (comparableReferences.some((reference) => reference === candidate)) return true;
    if (candidate.length < 12) {
      return comparableReferences.some((reference) => reference.includes(candidate));
    }

    const candidateGrams = characterNgrams(candidate, 3);
    const referenceGrams = new Set(
      comparableReferences.flatMap((reference) => [...characterNgrams(reference, 3)])
    );
    if (!candidateGrams.size || !referenceGrams.size) return false;
    let shared = 0;
    candidateGrams.forEach((gram) => {
      if (referenceGrams.has(gram)) shared += 1;
    });
    return shared / candidateGrams.size >= 0.68;
  }

  function comparisonText(value) {
    return String(value)
      .normalize("NFKC")
      .toLocaleLowerCase("ja")
      .replace(/[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+/g, "");
  }

  function characterNgrams(value, width) {
    const grams = new Set();
    if (value.length < width) {
      if (value) grams.add(value);
      return grams;
    }
    for (let index = 0; index <= value.length - width; index += 1) {
      grams.add(value.slice(index, index + width));
    }
    return grams;
  }

  function splitSentences(value) {
    return String(value).match(/[^。！？!?]+[。！？!?]?/g)?.map((part) => part.trim()).filter(Boolean) || [];
  }

  function isProceduralDetailSentence(value) {
    const text = paragraphKey(value);
    return /は「.+」と報じました[。.]?$/.test(text);
  }

  function isProceduralKeyPoint(value) {
    const text = paragraphKey(value);
    return [
      /rss(?:の)?(?:見出し|フィード)/i,
      /(?:見出し|配信概要|公開時刻).*(?:使用|参照|取得)/,
      /(?:情報源|出典).*(?:使用|参照|表示)/,
      /(?:記事|要約).*(?:作成|生成).*(?:使用|参照)?/,
      /(?:公開情報|提供情報|公開データ).*(?:もと|基に).*(?:要約|編集|加工)/
    ].some((pattern) => pattern.test(text));
  }

  function isMaterialUpdate(update) {
    if (!update?.text || update.material === false) return false;
    if (update.material === true) return true;

    const kind = String(update.kind || "").trim().toLocaleLowerCase("ja");
    if (["automatic", "auto", "system", "scheduled", "metadata"].includes(kind)) return false;
    if (["material", "correction", "訂正", "追記"].includes(kind)) return true;

    const text = paragraphKey(update.text);
    return ![
      /^元情報の公開時刻$/,
      /^この要約の最終更新$/,
      /^(?:要約・出典情報|要約と出典情報|出典情報|要約)を更新$/,
      /^(?:この記事|記事データ|記事|要約)を(?:自動)?(?:作成|生成|公開)$/,
      /^(?:定期|自動)更新$/,
      /の公開情報を確認$/
    ].some((pattern) => pattern.test(text));
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

  function editionOriginLabel(article) {
    const date = formatDate(article.editionGeneratedAt || article.updatedAt, {
      month: "long",
      day: "numeric"
    });
    return `${date} ${article.editionLabel || "過去の号"}`;
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
