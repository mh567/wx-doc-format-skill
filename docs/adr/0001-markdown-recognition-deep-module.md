# Markdown 识别采用单一深 module

Markdown 识别固定在 Source Document 到 DocumentModel 的 adapter seam，并通过一个纯计算 interface 返回模型、Diagnostic 和合同。首版使用内部规范表和 dispatch，不开放方言、插件或裁决策略注册表，因为当前只有一个真实 tokenizer 和一套生产识别策略；该选择保持调用 interface 较小，并把 token 遍历、Container Context、Recognition Decision、Fallback Representation 和合同校验集中在同一 module。
