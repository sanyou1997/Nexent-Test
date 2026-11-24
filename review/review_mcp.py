from mcp.server.fastmcp import FastMCP
from review import review
import local_rules
def source_state():
    return local_rules.source_check()
def lookup_rule(title,number):
    return local_rules.article(title,number)
server=FastMCP("案件审理核查",host="0.0.0.0",port=8096)
@server.tool()
def review_report(report: str) -> dict:
    """核对完整报告文本的明确法规引用、附件编号和模板金额。传入逐行原文，返回行号和权威原文。"""
    state=source_state()
    result=review(report,lookup=lookup_rule) if state['ok'] else review(report)
    if not state["ok"]:
        result["citations"]=[]
        result["findings"]=[f for f in result["findings"] if f["code"] not in {"查无条文","版本待核","引文不符","细分条款待核","未取得可核条文"}]
        result["findings"].append(dict(line=None,code="法规未核验",detail="暂停法规意见，待复核"))
    return dict(source_check=state,**result)
@server.tool()
def get_rule(title: str, number: int) -> dict:
    """按完整法规名称和阿拉伯数字条号返回本地原文；历史版本在名称后加（2018年修订）。无匹配会明确返回。"""
    state=source_state()
    return dict(source_check=state,**lookup_rule(title,number)) if state["ok"] else state
if __name__=="__main__":
    server.run(transport="sse")
