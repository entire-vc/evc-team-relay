<script lang="ts">
  import { Tabs as TabsPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = TabsPrimitive.RootProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<TabsPrimitive.Root class={cn('', className)} {...restProps}>
  {@render children?.()}
</TabsPrimitive.Root>
