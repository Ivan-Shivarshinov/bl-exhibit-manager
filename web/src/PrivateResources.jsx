import React,{useEffect,useState} from 'react';
import {ownerFetch,downloadResource} from './access';

export function PrivateImage({src,...props}) {
  const [url,setUrl]=useState('');
  const [error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController();let live=true,objectUrl;setUrl('');setError('');
    ownerFetch(src,{signal:controller.signal}).then(response=>{if(!response.ok)throw Error('Предпросмотр недоступен. Повторите открытие документа или запуск приложения.');return response.blob();})
      .then(blob=>{if(live){objectUrl=URL.createObjectURL(blob);setUrl(objectUrl);}}).catch(error=>{if(live&&error.name!=='AbortError'){setError(error.message);props.onError?.(error);}});
    return()=>{live=false;controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[src]);
  return <><img {...props} src={url||undefined}/>{error&&<span role="alert" className="error-note">{error}</span>}</>;
}

export function PrivateLink({href,download,children,...props}) {
  const [error,setError]=useState('');
  async function open(event) {
    event.preventDefault();setError('');let popup;
    try {
      if(props.target!=='_blank'||download!==undefined){await downloadResource(href,typeof download==='string'?download:'');return;}
      popup=window.open('about:blank','_blank');if(popup)popup.opener=null;
      const response=await ownerFetch(href);if(!response.ok)throw Error('Файл недоступен. Запустите приложение повторно и проверьте доступ.');
      const url=URL.createObjectURL(await response.blob());
      if(popup)popup.location.replace(url);else window.location.assign(url);
      setTimeout(()=>URL.revokeObjectURL(url),60000);
    }catch(e){popup?.close();setError(e.message);}
  }
  return <><a {...props} href={href} download={download} onClick={open}>{children}</a>{error&&<span role="alert">{error}</span>}</>;
}
