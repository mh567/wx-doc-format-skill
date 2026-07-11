#!/usr/bin/env python3
"""Setup input directory for benchmark tests."""
import os, shutil

# Create input subdirectories
os.makedirs("input/接口类", exist_ok=True)
os.makedirs("input/方案类", exist_ok=True)

# Copy files from wx_output locations
copies = [
    ("wx_output/api_doc2/接口控制文件2.docx", "input/接口类/接口控制文件2.docx"),
    ("wx_output/api_doc/接口文档0708.docx", "input/接口类/接口文档0708.docx"),
    ("wx_output/batch_test/身份接口策略下发部分.docx", "input/接口类/身份接口策略下发部分.docx"),
    ("wx_output/batch_test/IAM全球部署高可用.docx", "input/方案类/IAM全球部署高可用.docx"),
    ("wx_output/batch_test/生物特征认证系统集成方案.docx", "input/方案类/生物特征认证系统集成方案.docx"),
    ("wx_output/batch_test/身份对象模型.docx", "input/方案类/身份对象模型.docx"),
]

for src, dst in copies:
    if os.path.exists(src):
        shutil.copy2(src, dst)
        size = os.path.getsize(dst)
        print(f"  {dst} ({size/1024:.0f}K)")
    else:
        print(f"  MISSING: {src}")

print("\nDone.")
