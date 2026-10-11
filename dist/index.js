const manifest = {"name":"AutoWhite"};
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
} catch (e) {
    api = internalAPIConnection.connect(1, manifest.name);
}
const callable = api.callable;
const definePlugin = (fn) => (...args) => fn(...args);

const getState = callable("get_state");
const setSettings = callable("set_settings");
const resetSettings = callable("reset_settings");
const checkUpdate = callable("check_update");

const LANG = (() => {
    const raw = (navigator.language || (navigator.languages && navigator.languages[0]) || "en").toLowerCase();
    return raw.indexOf("ru") === 0 ? "ru" : "en";
})();

const S = {
    title: ["Точка белого", "White point"],
    enabled: ["Включено", "Enabled"],
    enabledDesc: ["Экран теплее в темноте и нейтральнее при ярком свете", "Warmer in the dark, neutral in bright light"],
    warmth: ["Холоднее — Теплее", "Cooler — Warmer"],
    state: ["Состояние", "State"],
    lux: ["Освещённость", "Illuminance"],
    white: ["Точка белого", "White point"],
    noSensor: ["датчик не найден", "sensor not found"],
    luxUnit: [" лк", " lx"],
    unknown: ["неизвестно", "unknown"],
    target: ["Цель", "Target"],
    update: ["Обновления", "Updates"],
    updateButton: ["Проверить обновление", "Check for updates"],
    updateChecking: ["Проверяю...", "Checking..."],
    updateStatus: ["Версия", "Version"],
    updateNone: ["Обновлений нет", "No updates"],
    updateAvailable: ["Доступна версия", "Version available"],
    updateNoReleases: ["Релизов на GitHub пока нет", "No releases on GitHub yet"],
    updateError: ["Не удалось проверить — нет связи с GitHub", "Check failed — no connection to GitHub"]
};
const t = (key) => { const v = S[key]; return Array.isArray(v) ? v[LANG === "ru" ? 0 : 1] : key; };

// Иконка панели: половина диска залита — «свет и тень», как на шапке.
const WhiteIcon = () => SP_JSX.jsx("svg", {
    width: "1em",
    height: "1em",
    viewBox: "0 0 24 24",
    fill: "none",
    xmlns: "http://www.w3.org/2000/svg",
    children: [
        SP_JSX.jsx("g", {
            stroke: "currentColor",
            strokeWidth: 1.8,
            strokeLinecap: "round",
            children: [
                SP_JSX.jsx("circle", { cx: 12, cy: 12, r: 5.5 }, "disc"),
                SP_JSX.jsx("path", {
                    d: "M12 2v1.5M12 20.5V22M2 12h1.5M20.5 12H22"
                        + "M4.93 4.93l1.06 1.06M18.01 18.01l1.06 1.06"
                        + "M19.07 4.93l-1.06 1.06M5.99 18.01l-1.06 1.06"
                }, "rays")
            ]
        }, "ring"),
        SP_JSX.jsx("path", { d: "M12 6.5a5.5 5.5 0 0 0 0 11Z", fill: "currentColor" }, "half")
    ]
});

const row = (child, key) => SP_JSX.jsx(DFL.PanelSectionRow, { children: child }, key);
const section = (title, children, key) => SP_JSX.jsx(DFL.PanelSection, { title: title, children: children }, key);

const Content = () => {
    const [state, setState] = SP_REACT.useState({});
    const [settings, setLocal] = SP_REACT.useState(null);
    const [updateBusy, setUpdateBusy] = SP_REACT.useState(false);
    const [updateText, setUpdateText] = SP_REACT.useState("");
    const pending = SP_REACT.useRef(null);
    const timer = SP_REACT.useRef(null);

    const apply = (resp) => {
        if (!resp) return;
        setState(resp.state || {});
        setLocal(resp.settings || {});
    };
    const refresh = SP_REACT.useCallback(async () => {
        try { apply(await getState()); } catch (e) { }
    }, []);
    SP_REACT.useEffect(() => {
        refresh();
        const id = setInterval(refresh, 3000);
        return () => clearInterval(id);
    }, [refresh]);

    const flush = SP_REACT.useCallback(async () => {
        const patchData = pending.current;
        pending.current = null;
        if (!patchData) return;
        try { apply(await setSettings(patchData)); } catch (e) { }
    }, []);
    const patch = SP_REACT.useCallback((partial) => {
        setLocal((prev) => Object.assign({}, prev || {}, partial));
        pending.current = Object.assign({}, pending.current || {}, partial);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => { timer.current = null; flush(); }, 400);
    }, [flush]);

    const runUpdateCheck = async () => {
        if (updateBusy) return;
        setUpdateBusy(true);
        setUpdateText(t("updateChecking"));
        let data = null;
        try { data = await checkUpdate(); } catch (e) { }
        setUpdateBusy(false);
        if (!data || data.error) {
            setUpdateText(data && data.code === 404 ? t("updateNoReleases") : t("updateError"));
        } else if (data.has_update) {
            setUpdateText(t("updateAvailable") + " " + (data.latest || "?"));
        } else {
            setUpdateText(t("updateNone") + " (" + (data.current || "?") + ")");
        }
    };

    if (!settings) return SP_JSX.jsx("div", { children: "" });

    const st = state || {};
    const whiteText = st.white
        ? st.white[0].toFixed(4) + " / " + st.white[1].toFixed(4)
        : t("unknown");
    const targetText = st.target
        ? st.target[0].toFixed(4) + " / " + st.target[1].toFixed(4)
        : "—";
    const luxText = (st.lux === null || st.lux === undefined)
        ? t("noSensor") : st.lux + t("luxUnit");


    const controls = [
        row(SP_JSX.jsx(DFL.ToggleField, {
            label: t("enabled"), description: t("enabledDesc"),
            checked: !!settings.enabled,
            onChange: (v) => { if (v !== !!settings.enabled) patch({ enabled: v }); }
        }), "en"),
        row(SP_JSX.jsx(DFL.SliderField, {
            label: t("warmth"), value: Math.round((settings.warmth || 0) * 100),
            min: 0, max: 100, step: 5, showValue: true,
            onChange: (v) => {
                // The Decky panel sometimes fires onChange while rendering, so
                // only write when the value really changed.
                if (Math.round((settings.warmth || 0) * 100) !== v) patch({ warmth: v / 100 });
            }
        }), "warm")
    ];

    return SP_JSX.jsx(SP_REACT.Fragment, {
        children: [
            section(t("title"), controls, "main"),
            section(t("state"), [
                row(SP_JSX.jsx(DFL.Field, { label: t("lux"), children: luxText }), "lux"),
                row(SP_JSX.jsx(DFL.Field, { label: t("white"), children: whiteText }), "wp"),
                row(SP_JSX.jsx(DFL.Field, { label: t("target"), children: targetText }), "target")
            ], "state"),
            section(t("update"), [
                row(SP_JSX.jsx(DFL.ButtonItem, {
                    layout: "below",
                    bottomSeparator: updateText ? "standard" : "none",
                    disabled: updateBusy,
                    children: updateBusy ? t("updateChecking") : t("updateButton"),
                    onClick: runUpdateCheck
                }), "update-button"),
                updateText
                    ? row(SP_JSX.jsx(DFL.Field, {
                        label: t("updateStatus"), children: updateText
                    }), "update-result")
                    : null
            ].filter(Boolean), "update")
        ]
    });
};

var index = definePlugin(() => ({
    name: "AutoWhite",
    titleView: SP_JSX.jsx("div", { className: DFL.staticClasses.Title, children: "AutoWhite" }),
    content: SP_JSX.jsx(Content, {}),
    icon: SP_JSX.jsx(WhiteIcon, {}),
    onDismount() { }
}));

export { index as default };
