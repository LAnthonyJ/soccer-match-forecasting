const clubColours: [RegExp,string,string][] = [
  [/arsenal/i,'#ca3741','ARS'],[/manchester city|man city/i,'#71add3','MCI'],[/liverpool/i,'#b93947','LIV'],
  [/manchester united|man utd/i,'#cf4e46','MUN'],[/chelsea/i,'#4469b0','CHE'],[/tottenham/i,'#8796b9','TOT'],
  [/aston villa/i,'#964c69','AVL'],[/newcastle/i,'#5d6872','NEW'],[/brighton/i,'#417bb6','BHA'],
  [/bournemouth/i,'#b35149','BOU'],[/brentford/i,'#c35655','BRE'],[/crystal palace/i,'#565eb7','CRY'],
  [/everton/i,'#396cbe','EVE'],[/fulham/i,'#75818c','FUL'],[/west ham/i,'#9d546f','WHU'],
  [/wolv/i,'#a27419','WOL'],[/nottingham/i,'#b8444c','NFO'],[/leeds/i,'#8c7733','LEE'],
  [/leicester/i,'#4273b5','LEI'],[/sunderland/i,'#b65257','SUN'],[/burnley/i,'#944a60','BUR'],
]
export function club(team:string):[string,string] {
  const found=clubColours.find(([re])=>re.test(team))
  if(found) return [found[1],found[2]]
  return ['#437c73',team.replace(/[^A-Za-z]/g,'').slice(0,3).toUpperCase() || 'FC']
}
export function TeamMark({team,small=false}:{team:string;small?:boolean}) {
  const [colour,code]=club(team)
  return <span aria-hidden="true" className={'team-mark'+(small?' mini':'')} style={{'--team':colour} as React.CSSProperties}>{small?'':code}</span>
}
