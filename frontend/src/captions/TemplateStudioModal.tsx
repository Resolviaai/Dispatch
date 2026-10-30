import React from 'react';
import { TemplateStudio, TemplateStudioProps } from './TemplateStudio';

export type TemplateStudioModalProps = TemplateStudioProps;

export const TemplateStudioModal: React.FC<TemplateStudioModalProps> = (props) => {
  return <TemplateStudio {...props} />;
};

export { TemplateStudio };
export default TemplateStudioModal;
