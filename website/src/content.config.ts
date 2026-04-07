import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const stories = defineCollection({
	loader: glob({ pattern: '**/*.mdx', base: './src/content/stories' }),
	schema: z.object({
		title: z.string(),
		description: z.string(),
		publishDate: z.string(),
		ogImage: z.string().optional(),
		tags: z.array(z.string()).optional(),
	}),
});

export const collections = { stories };
