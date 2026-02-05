<script lang="ts">
  import { Slider as SliderPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = SliderPrimitive.RootProps & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<SliderPrimitive.Root
  class={cn('relative flex w-full touch-none select-none items-center', className)}
  {...restProps}
>
  {#snippet children({ thumbs })}
    <SliderPrimitive.Range class="absolute h-full bg-primary" />
    {#each thumbs as index}
      <SliderPrimitive.Thumb
        {index}
        class="block h-5 w-5 rounded-full border-2 border-primary bg-background ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50"
      />
    {/each}
  {/snippet}
</SliderPrimitive.Root>
