(function exposeMarkdownFileHelpers(root, factory) {
    const helpers = factory();
    if (typeof module !== "undefined" && module.exports) module.exports = helpers;
    root.MarkdownFile = helpers;
}(typeof globalThis !== "undefined" ? globalThis : this, function createHelpers() {
    const MAX_BYTES = 5 * 1024 * 1024;
    function validExtension(name) { return /\.(md|markdown)$/i.test(String(name)); }
    function titleFromFilename(name) { return String(name).replace(/\.(md|markdown)$/i, ""); }
    function validate(file) {
        if (!file || !validExtension(file.name)) return "Selecione um arquivo .md ou .markdown.";
        if (file.size > MAX_BYTES) return "O arquivo excede o limite de 5 MiB.";
        return null;
    }
    return {MAX_BYTES, validExtension, titleFromFilename, validate};
}));
