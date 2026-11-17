// setup 组件：工具清单见 manifests/setup.js。
import { featureComponent } from './feature.js';

const component = featureComponent('setup');
export const name = component.name;
export const inject = component.inject;
export const apply = component.apply;
