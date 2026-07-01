from dataclasses import dataclass


def normalize_team_query(value: str) -> str:
    return "".join(ch.lower() for ch in (value or "").strip() if ch.isalnum())


@dataclass(frozen=True)
class TeamAlias:
    display_name: str
    sportmonks_name: str
    aliases: tuple[str, ...]


TEAM_ALIASES = [
    TeamAlias("阿根廷", "Argentina", ("Argentina", "阿根廷", "阿根廷队")),
    TeamAlias("澳大利亚", "Australia", ("Australia", "澳大利亚")),
    TeamAlias("奥地利", "Austria", ("Austria", "奥地利")),
    TeamAlias("比利时", "Belgium", ("Belgium", "比利时")),
    TeamAlias("玻利维亚", "Bolivia", ("Bolivia", "玻利维亚")),
    TeamAlias("波黑", "Bosnia and Herzegovina", ("Bosnia and Herzegovina", "Bosnia", "波黑", "波斯尼亚和黑塞哥维那")),
    TeamAlias("巴西", "Brazil", ("Brazil", "巴西")),
    TeamAlias("保加利亚", "Bulgaria", ("Bulgaria", "保加利亚")),
    TeamAlias("喀麦隆", "Cameroon", ("Cameroon", "喀麦隆")),
    TeamAlias("加拿大", "Canada", ("Canada", "加拿大")),
    TeamAlias("智利", "Chile", ("Chile", "智利")),
    TeamAlias("中国", "China", ("China", "中国", "中国队")),
    TeamAlias("哥伦比亚", "Colombia", ("Colombia", "哥伦比亚")),
    TeamAlias("哥斯达黎加", "Costa Rica", ("Costa Rica", "哥斯达黎加")),
    TeamAlias("克罗地亚", "Croatia", ("Croatia", "克罗地亚")),
    TeamAlias("捷克", "Czech Republic", ("Czech Republic", "Czechia", "捷克")),
    TeamAlias("丹麦", "Denmark", ("Denmark", "丹麦")),
    TeamAlias("厄瓜多尔", "Ecuador", ("Ecuador", "厄瓜多尔")),
    TeamAlias("埃及", "Egypt", ("Egypt", "埃及")),
    TeamAlias("英格兰", "England", ("England", "英格兰")),
    TeamAlias("法国", "France", ("France", "法国")),
    TeamAlias("德国", "Germany", ("Germany", "德国")),
    TeamAlias("加纳", "Ghana", ("Ghana", "加纳")),
    TeamAlias("希腊", "Greece", ("Greece", "希腊")),
    TeamAlias("洪都拉斯", "Honduras", ("Honduras", "洪都拉斯")),
    TeamAlias("匈牙利", "Hungary", ("Hungary", "匈牙利")),
    TeamAlias("冰岛", "Iceland", ("Iceland", "冰岛")),
    TeamAlias("伊朗", "Iran", ("Iran", "IR Iran", "伊朗")),
    TeamAlias("伊拉克", "Iraq", ("Iraq", "伊拉克")),
    TeamAlias("以色列", "Israel", ("Israel", "以色列")),
    TeamAlias("意大利", "Italy", ("Italy", "意大利")),
    TeamAlias("牙买加", "Jamaica", ("Jamaica", "牙买加")),
    TeamAlias("日本", "Japan", ("Japan", "日本")),
    TeamAlias("约旦", "Jordan", ("Jordan", "约旦")),
    TeamAlias("韩国", "Korea Republic", ("Korea Republic", "South Korea", "韩国", "南韩")),
    TeamAlias("墨西哥", "Mexico", ("Mexico", "墨西哥")),
    TeamAlias("摩洛哥", "Morocco", ("Morocco", "摩洛哥")),
    TeamAlias("荷兰", "Netherlands", ("Netherlands", "Holland", "荷兰")),
    TeamAlias("新西兰", "New Zealand", ("New Zealand", "新西兰")),
    TeamAlias("尼日利亚", "Nigeria", ("Nigeria", "尼日利亚")),
    TeamAlias("挪威", "Norway", ("Norway", "挪威")),
    TeamAlias("巴拿马", "Panama", ("Panama", "巴拿马")),
    TeamAlias("巴拉圭", "Paraguay", ("Paraguay", "巴拉圭")),
    TeamAlias("秘鲁", "Peru", ("Peru", "秘鲁")),
    TeamAlias("波兰", "Poland", ("Poland", "波兰")),
    TeamAlias("葡萄牙", "Portugal", ("Portugal", "葡萄牙")),
    TeamAlias("卡塔尔", "Qatar", ("Qatar", "卡塔尔")),
    TeamAlias("爱尔兰", "Republic of Ireland", ("Republic of Ireland", "Ireland", "爱尔兰")),
    TeamAlias("罗马尼亚", "Romania", ("Romania", "罗马尼亚")),
    TeamAlias("俄罗斯", "Russia", ("Russia", "俄罗斯")),
    TeamAlias("沙特阿拉伯", "Saudi Arabia", ("Saudi Arabia", "沙特阿拉伯", "沙特")),
    TeamAlias("苏格兰", "Scotland", ("Scotland", "苏格兰")),
    TeamAlias("塞内加尔", "Senegal", ("Senegal", "塞内加尔")),
    TeamAlias("塞尔维亚", "Serbia", ("Serbia", "塞尔维亚")),
    TeamAlias("斯洛伐克", "Slovakia", ("Slovakia", "斯洛伐克")),
    TeamAlias("斯洛文尼亚", "Slovenia", ("Slovenia", "斯洛文尼亚")),
    TeamAlias("南非", "South Africa", ("South Africa", "南非")),
    TeamAlias("西班牙", "Spain", ("Spain", "西班牙")),
    TeamAlias("瑞典", "Sweden", ("Sweden", "瑞典")),
    TeamAlias("瑞士", "Switzerland", ("Switzerland", "瑞士")),
    TeamAlias("突尼斯", "Tunisia", ("Tunisia", "突尼斯")),
    TeamAlias("土耳其", "Turkey", ("Turkey", "Turkiye", "Türkiye", "土耳其")),
    TeamAlias("阿联酋", "United Arab Emirates", ("United Arab Emirates", "UAE", "阿联酋")),
    TeamAlias("美国", "United States", ("United States", "USA", "US", "美国")),
    TeamAlias("乌拉圭", "Uruguay", ("Uruguay", "乌拉圭")),
    TeamAlias("委内瑞拉", "Venezuela", ("Venezuela", "委内瑞拉")),
    TeamAlias("威尔士", "Wales", ("Wales", "威尔士")),
    TeamAlias("阿尔巴尼亚", "Albania", ("Albania", "阿尔巴尼亚")),
    TeamAlias("阿尔及利亚", "Algeria", ("Algeria", "阿尔及利亚")),
    TeamAlias("安哥拉", "Angola", ("Angola", "安哥拉")),
    TeamAlias("亚美尼亚", "Armenia", ("Armenia", "亚美尼亚")),
    TeamAlias("阿塞拜疆", "Azerbaijan", ("Azerbaijan", "阿塞拜疆")),
    TeamAlias("巴林", "Bahrain", ("Bahrain", "巴林")),
    TeamAlias("白俄罗斯", "Belarus", ("Belarus", "白俄罗斯")),
    TeamAlias("布基纳法索", "Burkina Faso", ("Burkina Faso", "布基纳法索")),
    TeamAlias("刚果民主共和国", "Congo DR", ("Congo DR", "DR Congo", "刚果民主共和国", "刚果金")),
    TeamAlias("塞浦路斯", "Cyprus", ("Cyprus", "塞浦路斯")),
    TeamAlias("爱沙尼亚", "Estonia", ("Estonia", "爱沙尼亚")),
    TeamAlias("芬兰", "Finland", ("Finland", "芬兰")),
    TeamAlias("格鲁吉亚", "Georgia", ("Georgia", "格鲁吉亚")),
    TeamAlias("危地马拉", "Guatemala", ("Guatemala", "危地马拉")),
    TeamAlias("印度", "India", ("India", "印度")),
    TeamAlias("印度尼西亚", "Indonesia", ("Indonesia", "印尼", "印度尼西亚")),
    TeamAlias("科威特", "Kuwait", ("Kuwait", "科威特")),
    TeamAlias("拉脱维亚", "Latvia", ("Latvia", "拉脱维亚")),
    TeamAlias("黎巴嫩", "Lebanon", ("Lebanon", "黎巴嫩")),
    TeamAlias("利比亚", "Libya", ("Libya", "利比亚")),
    TeamAlias("立陶宛", "Lithuania", ("Lithuania", "立陶宛")),
    TeamAlias("卢森堡", "Luxembourg", ("Luxembourg", "卢森堡")),
    TeamAlias("马来西亚", "Malaysia", ("Malaysia", "马来西亚")),
    TeamAlias("马里", "Mali", ("Mali", "马里")),
    TeamAlias("摩尔多瓦", "Moldova", ("Moldova", "摩尔多瓦")),
    TeamAlias("黑山", "Montenegro", ("Montenegro", "黑山")),
    TeamAlias("朝鲜", "Korea DPR", ("Korea DPR", "North Korea", "朝鲜")),
    TeamAlias("北爱尔兰", "Northern Ireland", ("Northern Ireland", "北爱尔兰")),
    TeamAlias("阿曼", "Oman", ("Oman", "阿曼")),
    TeamAlias("菲律宾", "Philippines", ("Philippines", "菲律宾")),
    TeamAlias("叙利亚", "Syria", ("Syria", "叙利亚")),
    TeamAlias("泰国", "Thailand", ("Thailand", "泰国")),
    TeamAlias("乌克兰", "Ukraine", ("Ukraine", "乌克兰")),
    TeamAlias("乌兹别克斯坦", "Uzbekistan", ("Uzbekistan", "乌兹别克斯坦")),
    TeamAlias("越南", "Vietnam", ("Vietnam", "越南")),
    TeamAlias("赞比亚", "Zambia", ("Zambia", "赞比亚")),
]


def _row(team: TeamAlias) -> dict:
    return {
        "display_name": team.display_name,
        "sportmonks_name": team.sportmonks_name,
        "aliases": list(team.aliases),
    }


def local_team_alias_rows() -> list[dict]:
    return [_row(team) for team in TEAM_ALIASES]


def merge_team_rows(extra_rows: list[dict] | None = None) -> list[dict]:
    rows_by_name: dict[str, dict] = {}
    for row in local_team_alias_rows() + list(extra_rows or []):
        name = row["sportmonks_name"]
        existing = rows_by_name.get(name)
        if existing is None:
            rows_by_name[name] = {
                "display_name": row.get("display_name") or name,
                "sportmonks_name": name,
                "aliases": list(dict.fromkeys(row.get("aliases", []) + [name])),
            }
            continue
        existing["display_name"] = existing.get("display_name") or row.get("display_name") or name
        existing["aliases"] = list(dict.fromkeys(existing["aliases"] + row.get("aliases", []) + [name]))
    return list(rows_by_name.values())


def search_team_aliases(query: str, limit: int = 10, extra_rows: list[dict] | None = None) -> list[dict]:
    normalized = normalize_team_query(query)
    if not normalized:
        return []

    exact = []
    prefix = []
    partial = []
    for row in merge_team_rows(extra_rows):
        aliases = row.get("aliases", []) + [row.get("display_name", ""), row.get("sportmonks_name", "")]
        normalized_aliases = [normalize_team_query(alias) for alias in aliases]
        if normalized in normalized_aliases:
            exact.append(row)
        elif any(alias.startswith(normalized) for alias in normalized_aliases):
            prefix.append(row)
        elif any(normalized in alias for alias in normalized_aliases):
            partial.append(row)

    results = []
    seen = set()
    for row in exact + prefix + partial:
        name = row["sportmonks_name"]
        if name in seen:
            continue
        seen.add(name)
        results.append(row)
        if len(results) >= limit:
            break
    return results
