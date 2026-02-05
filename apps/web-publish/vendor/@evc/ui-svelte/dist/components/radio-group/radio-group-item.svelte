<script lang="ts">
  import { RadioGroup as RadioGroupPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = RadioGroupPrimitive.ItemProps & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<RadioGroupPrimitive.Item
  class={cn(
    'aspect-square h-4 w-4 rounded-full border border-primary text-primary shadow focus:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
    className
  )}
  {...restProps}
>
  {#snippet children({ checked })}
    {#if checked}
      <span class="flex items-center justify-center">
        <div class="h-2 w-2 rounded-full bg-primary"></div>
      </span>
    {/if}
  {/snippet}
</RadioGroupPrimitive.Item>
