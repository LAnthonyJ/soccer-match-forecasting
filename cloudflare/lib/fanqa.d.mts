export interface QuestionContext {fixture_id:string;topic:string;fact_hash?:string}
type Fact={id:string;text:string;tool:string;stage:string}
type Packet={fixture_id:string;fact_hash:string;facts:Fact[];as_of:string}
export interface FanAnswer {answer_fixture_id:string;answer_fact_hash:string;fixture_label:string;answer_kickoff?:string;standings?:{team:string;team_zh:string;rank:number;played:number;wins:number;draws:number;losses:number;gf:number;ga:number;points:number}[];topic:string;as_of:string;evidence:string[];missing_reason:string|null;source:string;answer:string;needs_choice?:boolean;choices:{id:string;label:string}[]}
export function suggestedQuestions(facts:{id:string;text:string}[]):string[];
export function answerFanQuestion(options:{packet:Packet;question:string;context?:QuestionContext;timezone:string;now?:Date;loadPacket:(id:string)=>Promise<Packet>;loadCatalog:()=>Promise<unknown>;loadHistory:(id:string,scope:string)=>Promise<unknown>;loadReview:(id:string)=>Promise<unknown>}):Promise<FanAnswer>;
