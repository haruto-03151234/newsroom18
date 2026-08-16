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
    archive: [],
    selectedCategory: "all",
    loadFailed: false
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
      "last-updated", "brief-summary", "lead-article", "bulletin-list", "category-filters",
      "important-grid", "news-sections", "result-count", "empty-state", "shorts-section", "shorts-list", "archive-list",
      "article-breadcrumb", "article-category", "article-importance", "article-title",
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
  }

  function renderLead() {
    clear(dom.leadArticle);
    const article = sortedByImportance(featureArticles(state.data.articles))[0];
    if (!article) {
      dom.leadArticle.append(element("p", "lead-article__empty", "この号は短報のみです。"));
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
    const leadId = sortedByImportance(featureArticles(state.data.articles))[0]?.id;
    const articles = [...state.data.articles]
      .filter((article) => article.id !== leadId)
      .sort((a, b) => dateValue(b.updatedAt) - dateValue(a.updatedAt))
      .slice(0, 5);

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
    let articles = sortedByImportance(featureArticles(state.data.articles)).filter((article) => article.importance >= 4);
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

    const features = featureArticles(articles);
    const briefs = briefArticles(articles);
    dom.resultCount.textContent = `詳報 ${features.length}本・短報 ${briefs.length}本`;
    dom.emptyState.hidden = Boolean(articles.length);

    renderShorts(briefs);

    for (const category of CATEGORY_ORDER) {
      const categoryArticles = features
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
      const linkedSources = article.sources.filter((source) => safeExternalUrl(source.url) !== "#");
      if (linkedSources.length) {
        linkedSources.forEach((source, index) => {
          if (index) sourceList.append(document.createTextNode(" / "));
          const link = document.createElement("a");
          link.href = safeExternalUrl(source.url);
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

  function storyCard(article, important = false) {
    const card = element("article", important ? "story-card story-card--important" : "story-card");
    const category = element("span", "story-card__category", categoryLabel(article.category));
    const title = headingLink(article, "h3");
    const summary = element("p", "story-card__summary", article.summary || article.dek);
    const meta = articleMeta(article, "story-meta");
    const sourceNames = article.sources.map((source) => source.name).join(" / ");
    const sources = element("p", "story-card__sources", `出典：${sourceNames || "リンク未掲載"}`);
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
        ? `配信元 ${article.sources.length}件（一次情報 ${primaryCount}件）`
        : `配信元 ${article.sources.length}件`;
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
      const link = document.createElement("a");
      link.href = safeExternalUrl(source.url);
      link.target = "_blank";
      link.rel = "noopener noreferrer nofollow";
      link.textContent = source.name;
      heading.append(link);
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
    const sources = element("span", "source-count", `${article.sources.length}配信元`);
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

  function newsDestinationLink(article, text) {
    if (article.articleType !== "brief") return articleLink(article, text);
    const source = article.sources.find((item) => safeExternalUrl(item.url) !== "#");
    if (!source) return element("span", "", text);
    const link = document.createElement("a");
    link.href = safeExternalUrl(source.url);
    link.target = "_blank";
    link.rel = "noopener noreferrer nofollow";
    link.textContent = text;
    return link;
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

    return {
      schemaVersion: raw.schemaVersion || 1,
      site: raw.site || {},
      generatedAt,
      summary: String(raw.summary || raw.description || ""),
      generationMode: String(raw.generationMode || ""),
      edition: {
        id,
        label: String(edition.label || edition.name || inferEditionLabel(generatedAt)),
        slot: String(edition.slot || formatTime(generatedAt))
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
    const articleType = normalizeArticleType(
      item.articleType || item.storyType || item.format || item.presentation,
      { facts, impactPoints, background, body, sections }
    );
    const rawDek = String(item.dek || item.subtitle || item.lead || "");
    const rawSummary = String(item.summary || item.description || item.dek || "");

    return {
      id,
      slug,
      title,
      dek: articleType === "brief" ? sanitizeBriefText(rawDek) : rawDek,
      summary: articleType === "brief" ? sanitizeBriefText(rawSummary) : rawSummary,
      category: normalizeCategory(item.category || item.section || "other"),
      importance: clamp(Number(item.importance ?? item.priority ?? 3), 1, 5),
      articleType,
      publishedAt,
      updatedAt,
      sources,
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
            keyPoints: []
          };
        }
        if (!source || typeof source !== "object") return null;
        return {
          name: String(source.name || source.publisher || source.title || `情報源 ${index + 1}`),
          url: String(source.url || source.link || "#"),
          publishedAt: validDate(source.publishedAt || source.published_at || source.date) || "",
          type: String(source.type || source.kind || ""),
          isPrimary: Boolean(source.isPrimary || source.primary),
          keyPoints: uniqueParagraphs(normalizeParagraphs(
            source.keyPoints || source.points || source.summary || source.note || []
          ))
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
        || /^.{1,100}の配信概要では、/.test(text);
      return !(sourceDirection || headlineAttribution || isProceduralKeyPoint(text));
    }).join("");
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
