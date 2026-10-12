import hostConfig from "../../../web-next/eslint.config.mjs";

const config = [...hostConfig, { rules: { "@next/next/no-html-link-for-pages": "off" } }];
export default config;
