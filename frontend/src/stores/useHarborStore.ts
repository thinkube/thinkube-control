import { create } from 'zustand';
import api from '@/lib/axios';

export interface HarborImage {
  id: string;
  name: string;
  tag: string;
  [key: string]: any;
}

interface HarborState {
  images: HarborImage[];
  loading: boolean;
  error: string | null;

  // Actions
  fetchImages: () => Promise<void>;
  addImage: (image: Partial<HarborImage>) => Promise<void>;
  deleteImage: (id: string) => Promise<void>;
}

export const useHarborStore = create<HarborState>((set) => ({
  images: [],
  loading: false,
  error: null,

  fetchImages: async () => {
    set({ loading: true, error: null });
    try {
      const response = await api.get('/harbor/images');
      set({ images: response.data, loading: false });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : 'Failed to fetch images',
        loading: false
      });
    }
  },

  addImage: async (image) => {
    try {
      const response = await api.post('/harbor/images', image);
      set(state => ({
        images: [...state.images, response.data]
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Failed to add image' });
      throw error;
    }
  },

  deleteImage: async (id) => {
    try {
      await api.delete(`/harbor/images/${id}`);
      set(state => ({
        images: state.images.filter(img => img.id !== id)
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Failed to delete image' });
      throw error;
    }
  },
}));
