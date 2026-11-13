/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TkTabsRoot, TkTabsList, TkTabsTrigger, TkTabsContent } from 'thinkube-style/components/navigation';
import { TkPageWrapper } from 'thinkube-style/components/utilities';
import { MirroredImagesTab } from '@/components/images/MirroredImagesTab';
import { CustomImagesTab } from '@/components/images/CustomImagesTab';

type Tab = 'mirrored' | 'custom';

/** The container images of the platform: the ones mirrored into Harbor, and the ones built from a Dockerfile. */
export default function ImagesPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('mirrored');

  return (
    <TkPageWrapper description={t('images.description')}>
      <TkTabsRoot value={tab} onValueChange={(value: string) => setTab(value as Tab)}>
        <TkTabsList>
          <TkTabsTrigger value="mirrored">{t('images.tabs.mirrored')}</TkTabsTrigger>
          <TkTabsTrigger value="custom">{t('images.tabs.custom')}</TkTabsTrigger>
        </TkTabsList>

        {/* Each tab is drawn only while it is chosen, so each reads its list when it is opened */}
        <TkTabsContent value="mirrored">{tab === 'mirrored' && <MirroredImagesTab />}</TkTabsContent>
        <TkTabsContent value="custom">{tab === 'custom' && <CustomImagesTab />}</TkTabsContent>
      </TkTabsRoot>
    </TkPageWrapper>
  );
}
