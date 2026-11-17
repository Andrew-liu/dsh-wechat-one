// persona 组件：工具清单见 manifests/persona.js。
import { featureComponent } from './feature.js';

const component = featureComponent('persona');
export const name = component.name;
export const inject = component.inject;
export const apply = component.apply;
