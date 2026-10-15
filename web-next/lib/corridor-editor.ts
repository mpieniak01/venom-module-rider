export type Corridor = [number,number,number,number];
export function imagePoint(px:number,py:number,width:number,height:number,imageWidth:number,imageHeight:number):[number,number]|null {
  if(Math.min(width,height,imageWidth,imageHeight)<=0)return null;
  const scale=Math.min(width/imageWidth,height/imageHeight),w=imageWidth*scale,h=imageHeight*scale;
  const x=(px-(width-w)/2)/w,y=(py-(height-h)/2)/h;
  return x<0||x>1||y<0||y>1?null:[x,y];
}
export function dragCorridor(start:Corridor,dx:number,dy:number,handle:"move"|"start"|"end"):Corridor {
  const [x0,y0,x1,y1]=start,clamp=(v:number,min:number,max:number)=>Math.max(min,Math.min(max,v));
  if(handle==="move"){dx=clamp(dx,-x0,1-x1);dy=clamp(dy,-y0,1-y1);return [x0+dx,y0+dy,x1+dx,y1+dy];}
  if(handle==="start")return [clamp(x0+dx,0,x1-.08),clamp(y0+dy,0,y1-.08),x1,y1];
  return [x0,y0,clamp(x1+dx,x0+.08,1),clamp(y1+dy,y0+.08,1)];
}
